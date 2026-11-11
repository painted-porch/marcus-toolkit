/**
 * Minimal MCP streamable-HTTP client for talking to a Marcus server.
 *
 * Lifted unchanged, but for the client name, from marcus-minecraft
 * src/marcus/mcp-http.js: the Node half of the shared client library
 * proposed in painted-porch/marcus issue 914.
 *
 * Marcus serves the Model Context Protocol over HTTP at `/mcp/`: one POST
 * for the `initialize` handshake, one notification to say the handshake is
 * done, then one POST per `tools/call`. Replies come back as a
 * Server-Sent-Events body whose `data:` lines carry JSON-RPC envelopes, and
 * a tool's own return value sits at `result.structuredContent.result`.
 *
 * This is deliberately a few dozen lines of `fetch` rather than the MCP SDK:
 * the agent image should not grow a dependency for three request shapes.
 */

/** Read the JSON-RPC envelopes out of an SSE (or plain JSON) body. */
export function parseSse(body) {
    const text = String(body ?? '');
    const envelopes = [];
    const lines = text.split(/\r?\n/);
    let sawData = false;
    for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        sawData = true;
        try {
            const parsed = JSON.parse(line.slice(5).trim());
            if (parsed && typeof parsed === 'object') envelopes.push(parsed);
        } catch {
            /* a partial or non-JSON data line is not ours */
        }
    }
    if (!sawData && text.trim().startsWith('{')) {
        try {
            const parsed = JSON.parse(text);
            if (parsed && typeof parsed === 'object') envelopes.push(parsed);
        } catch {
            /* not JSON either */
        }
    }
    return envelopes;
}

/** Pull a tool's structured return value out of a `tools/call` reply body. */
export function parseToolResult(body) {
    for (const envelope of parseSse(body)) {
        if (envelope.error)
            return {
                success: false,
                error: String(envelope.error.message ?? envelope.error),
                rpc_error: true,
            };
        const result = envelope.result;
        if (!result || typeof result !== 'object') continue;
        const structured = result.structuredContent;
        if (structured && typeof structured === 'object') {
            const inner = structured.result;
            if (inner && typeof inner === 'object') return inner;
            return structured;
        }
        const text = Array.isArray(result.content)
            ? result.content.find(c => c?.type === 'text' && typeof c.text === 'string')?.text
            : undefined;
        if (text) {
            try {
                const parsed = JSON.parse(text);
                if (parsed && typeof parsed === 'object') return parsed;
            } catch {
                return { success: !result.isError, message: text };
            }
        }
    }
    return null;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

export class McpHttpClient {
    /**
     * @param {string} url      the Marcus MCP endpoint, e.g. http://host:5441/mcp
     * @param {object} [opts]   fetchImpl, timeoutMs, retries, retryDelayMs, clientName
     */
    constructor(url, { fetchImpl = fetch, timeoutMs = 600000, retries = 3, retryDelayMs = 1000, clientName } = {}) {
        // A POST to `/mcp` is answered with a 307 that fetch replays as a GET,
        // so always send the trailing slash.
        this.url = String(url).replace(/\/+$/, '') + '/';
        this.fetchImpl = fetchImpl;
        this.timeoutMs = timeoutMs;
        this.retries = retries;
        this.retryDelayMs = retryDelayMs;
        this.clientName = clientName ?? 'marcus-runner';
        this.sessionId = '';
        this.nextId = 0;
    }

    get connected() {
        return Boolean(this.sessionId);
    }

    headers() {
        const headers = {
            'Content-Type': 'application/json',
            Accept: 'application/json, text/event-stream',
        };
        if (this.sessionId) headers['mcp-session-id'] = this.sessionId;
        return headers;
    }

    async post(payload, { expectBody = true } = {}) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        try {
            const response = await this.fetchImpl(this.url, {
                method: 'POST',
                headers: this.headers(),
                body: JSON.stringify(payload),
                signal: controller.signal,
            });
            const header = response.headers?.get?.('mcp-session-id');
            if (header) this.sessionId = header;
            if (!response.ok) {
                const detail = expectBody ? await response.text().catch(() => '') : '';
                throw new Error(`HTTP ${response.status} from Marcus${detail ? `: ${detail.slice(0, 300)}` : ''}`);
            }
            return expectBody ? await response.text() : '';
        } finally {
            clearTimeout(timer);
        }
    }

    /** Perform the handshake. Returns true when a session exists. */
    async connect() {
        await this.post({
            jsonrpc: '2.0',
            id: ++this.nextId,
            method: 'initialize',
            params: {
                protocolVersion: '2025-03-26',
                capabilities: {},
                clientInfo: { name: this.clientName, version: '1.0.0' },
            },
        });
        await this.post({ jsonrpc: '2.0', method: 'notifications/initialized' }, { expectBody: false });
        return this.connected;
    }

    /**
     * Call a Marcus tool. Never throws: a transport failure comes back as
     * `{success: false, error}` so the agent sees it as a tool result.
     */
    async call(name, args = {}) {
        let lastError = null;
        for (let attempt = 1; attempt <= Math.max(1, this.retries); attempt++) {
            try {
                if (!this.connected) await this.connect();
                const body = await this.post({
                    jsonrpc: '2.0',
                    id: ++this.nextId,
                    method: 'tools/call',
                    params: { name, arguments: args },
                });
                const result = parseToolResult(body);
                if (result) return result;
                lastError = new Error('Marcus returned no parseable result');
            } catch (error) {
                lastError = error;
                // A dropped session has to be re-established, not retried as is.
                if (/HTTP 40[04]/.test(String(error?.message))) this.sessionId = '';
            }
            if (attempt < Math.max(1, this.retries)) await sleep(this.retryDelayMs * attempt);
        }
        return {
            success: false,
            error: `Marcus call ${name} failed: ${String(lastError?.message ?? lastError)}`,
            transport_error: true,
        };
    }
}
