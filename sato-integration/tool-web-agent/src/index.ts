/**
 * Model-facing `web_agent` tool: a local Jev-style decision controller that
 * drives a headless browser (navigate → scroll/extract/download) using the
 * local gemma-jev decision server (Gemma 4 E4B on llama.cpp) for every
 * step decision. This package owns the schema, dispatch, spawn, and result
 * rendering; the Python controller (`~/gemma-jev/webagent.py`) owns the loop.
 *
 * Registers nothing by itself: the composition calls defineWebAgentTool and
 * registers the result, mirroring tool-browser-chrome.
 * @module @deepseek-ai/dsh-tool-web-agent
 */

import { spawn } from 'node:child_process'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import type { JsonValue } from '@deepseek-ai/dsh-util-values'

/** The tool name the model calls. */
export const WEB_AGENT_TOOL_NAME = 'web_agent'

export interface WebAgentToolOptions {
  /** Controller script path (headless variant). */
  script?: string
  /** CDP controller script path (drives the user's visible Chrome). */
  cdpScript?: string
  /** gemma-jev decision server URL. */
  jevUrl?: string | undefined
  /** Python interpreter with playwright + httpx. */
  python?: string
  /** Wall-clock budget for one run. */
  timeoutMs?: number
}

export interface WebAgentToolValue {
  url: string
  goal: string
  rounds: number
  downloads: number
  reportPath?: string
  verdicts: { round: number; verdict: string; p: number }[]
  files: { file: string; bytes: number; kind: string }[]
  collected?: { title: string; company: string; location: string; url: string }[]
}

const DEFAULT_SCRIPT = '/home/aisever/gemma-jev/webagent.py'
const CDP_SCRIPT = '/home/aisever/gemma-jev/webagent-cdp.py'
const DEFAULT_PYTHON = '/home/aisever/vllm-env/bin/python'
const DEFAULT_TIMEOUT_MS = 300_000

export function defineWebAgentTool(options: WebAgentToolOptions = {}) {
  const script = options.script ?? DEFAULT_SCRIPT
  const cdpScript = options.cdpScript ?? CDP_SCRIPT
  const python = options.python ?? DEFAULT_PYTHON
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  return defineTool({
    name: WEB_AGENT_TOOL_NAME,
    description: 'Run the local Jev-style decision controller on a web page: it opens the url in a headless browser, '
      + 'uses the local Gemma-4 decision model to decide each step (keep scrolling / extract / stop), then extracts '
      + 'page text and downloads goal-relevant files, saving everything under the run directory. Use for "go to this '
      + 'site, scroll as needed, and download the data" tasks where per-step speed matters. Returns a summary with '
      + 'per-round decision probabilities and the downloaded file list.',
    parameters: {
      url: { type: 'string', description: 'http(s) page to drive (headless mode).' },
      goal: { type: 'string', required: true, description: 'What to find or collect, one short line.' },
      query: { type: 'string', description: 'Search via the site\'s own search box in the user\'s visible Chrome (e.g. ".NET developer"). Uses CDP mode.' },
      location: { type: 'string', description: 'Optional location for the query (CDP mode).' },
      want: { type: 'number', description: 'Target item count (CDP mode), default 20.' },
      max_rounds: { type: 'number', description: 'Decide/scroll budget, default 12-15.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          url: { type: 'string', required: true },
          goal: { type: 'string', required: true },
          rounds: { type: 'number', required: true },
          downloads: { type: 'number', required: true },
          reportPath: { type: 'string' },
          verdicts: { type: 'array' },
          files: { type: 'array' },
        },
      },
      render: (_args, value: WebAgentToolValue): ContentBlock[] => [
        { type: 'text', text: `web_agent run on ${value.url}\ngoal: ${value.goal}\n`
          + `rounds: ${value.rounds}, downloads: ${value.downloads}\n`
          + value.verdicts.map(v => `r${v.round}: ${v.verdict} (p=${v.p})`).join('\n')
          + (value.files.length > 0 ? '\nfiles:\n' + value.files.map(f => `  ${f.file} (${f.bytes} b, ${f.kind})`).join('\n') : '') },
      ],
      presentationMeta: (_args, value: WebAgentToolValue): JsonValue => ({
        name: WEB_AGENT_TOOL_NAME,
        url: value.url,
        goal: value.goal,
        rounds: value.rounds,
        downloads: value.downloads,
      }) as unknown as JsonValue,
    },
    timeoutMs,
    isConcurrencySafe: () => false,
    async execute(args: { url?: string; goal: string; query?: string; location?: string; want?: number; max_rounds?: number }): Promise<WebAgentToolValue> {
      const outDir = await mkdtemp(join(tmpdir(), 'webagent-'))
      const env = { ...process.env }
      if (options.jevUrl !== undefined) env.GEMMAJEV_URL = options.jevUrl
      const useCdp = args.query !== undefined && args.query.trim() !== ''
      const scriptToRun = useCdp ? cdpScript : script
      const argv = [scriptToRun]
      if (useCdp) {
        if (args.url !== undefined && args.url.trim() !== '') argv.push(args.url)
        argv.push('--goal', args.goal, '--out', outDir)
        if (args.query !== undefined) argv.push('--query', args.query)
        if (args.location !== undefined && args.location.trim() !== '') argv.push('--location', args.location)
        if (args.want !== undefined) argv.push('--want', String(args.want))
      } else {
        if (args.url === undefined || args.url.trim() === '') throw new Error('the url or the query parameter is required')
        argv.push(args.url, '--goal', args.goal, '--out', outDir)
      }
      if (args.max_rounds !== undefined) argv.push('--max-rounds', String(args.max_rounds))
      const child = spawn(python, argv, { env, stdio: ['ignore', 'pipe', 'pipe'] })
      let out = ''
      child.stdout.on('data', (d: Buffer) => { out += d.toString() })
      child.stderr.on('data', (d: Buffer) => { out += d.toString() })
      const code = await new Promise<number>((resolveP, rejectP) => {
        const killer = setTimeout(() => { child.kill('SIGKILL'); rejectP(new Error(`web_agent timed out after ${timeoutMs}ms`)) }, timeoutMs)
        child.on('error', (e) => { clearTimeout(killer); rejectP(e) })
        child.on('close', (c) => { clearTimeout(killer); resolveP(c ?? 1) })
      })
      if (code !== 0) throw new Error(`web_agent controller failed (exit ${code}):\n${out.slice(-4000)}`)
      let report: any
      try {
        report = JSON.parse(await readFile(join(outDir, useCdp ? 'collected.json' : 'webagent_report.json'), 'utf8'))
      } catch {
        throw new Error(`web_agent produced no report:\n${out.slice(-4000)}`)
      }
      if (useCdp) {
        const collected: any[] = report.collected ?? []
        return {
          url: report.url ?? '',
          goal: report.goal,
          rounds: report.rounds?.length ?? 0,
          downloads: collected.length,
          reportPath: join(outDir, 'collected.json'),
          verdicts: (report.rounds ?? []).map((r: any) => ({ round: r.round, verdict: r.verdict, p: r.p })),
          files: [],
          collected: collected.map((c: any) => ({
            title: c.title ?? '', company: c.company ?? '',
            location: c.location ?? '', url: c.url ?? '',
          })),
        }
      }
      return {
        url: report.url,
        goal: report.goal,
        rounds: report.rounds.length,
        downloads: report.downloads.length,
        reportPath: join(outDir, 'webagent_report.json'),
        verdicts: report.rounds.map((r: any) => ({ round: r.round, verdict: r.verdict, p: r.probability ?? r.p })),
        files: report.downloads.map((d: any) => ({ file: d.file, bytes: d.bytes, kind: d.kind })),
      }
    },
  })
}
