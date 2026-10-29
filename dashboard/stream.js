'use strict';

// Fetch streams support X-API-Key and Last-Event-ID without putting secrets in URLs.
class SSEParser {
  constructor(onEvent) { this.buffer = ''; this.onEvent = onEvent; }
  feed(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > 1024 * 1024) throw new Error('SSE packet exceeded size limit');
    let match;
    while ((match = /\r?\n\r?\n/.exec(this.buffer))) {
      const packet = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      let type = 'message', id = null, data = [];
      for (const line of packet.split(/\r?\n/)) {
        if (!line || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (field === 'event') type = value;
        if (field === 'id') id = value;
        if (field === 'data') data.push(value);
      }
      if (data.length) this.onEvent({type, id, data: data.join('\n')});
    }
  }
}

class SSEClient {
  constructor(runId, handlers = {}, baseUrl = '/stream', getApiKey = () => '') {
    this.runId = runId;
    this.handlers = handlers;
    this.baseUrl = baseUrl;
    this.getApiKey = getApiKey;
    this.lastEventId = null;
    this.isConnected = false;
    this.isTerminated = false;
    this.reconnectDelay = 1000;
    this.controller = null;
    this.heartbeatTimer = null;
    this.reconnectTimer = null;
    this.task = null;
  }
  connect() {
    if (!this.task && !this.isTerminated) this.task = this._run();
    return this.task;
  }
  disconnect() {
    this.isTerminated = true;
    this.isConnected = false;
    this.controller?.abort();
    clearTimeout(this.heartbeatTimer);
    clearTimeout(this.reconnectTimer);
    this.resumeReconnect?.();
  }
  _watchdog() {
    clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => this.controller?.abort(), 45000);
  }
  _dispatch(event) {
    if (event.id !== null) this.lastEventId = event.id;
    if (event.type === 'ping') return;
    const data = JSON.parse(event.data);
    const handler = {progress: 'onProgress', score: 'onScore', done: 'onDone', error: 'onError'}[event.type];
    if (handler) this.handlers[handler]?.(data);
    if (event.type === 'done' || event.type === 'error') this.disconnect();
  }
  async _run() {
    while (!this.isTerminated) {
      this.controller = new AbortController();
      let reader;
      try {
        const headers = {Accept: 'text/event-stream'};
        const key = this.getApiKey();
        if (key) headers['X-API-Key'] = key;
        if (this.lastEventId !== null) headers['Last-Event-ID'] = this.lastEventId;
        const response = await fetch(`${this.baseUrl}/${encodeURIComponent(this.runId)}`, {headers, signal: this.controller.signal});
        if (response.status === 401 || response.status === 403) {
          this.handlers.onAuthError?.('Enter a valid API key to stream this run.');
          this.disconnect();
          return;
        }
        if (!response.ok || !response.body) throw new Error(`Stream HTTP ${response.status}`);
        this.isConnected = true;
        this.handlers.onConnectionChange?.('connected');
        this._watchdog();
        const parser = new SSEParser(event => this._dispatch(event));
        const decoder = new TextDecoder();
        reader = response.body.getReader();
        while (!this.isTerminated) {
          const {done, value} = await reader.read();
          if (done) break;
          this.reconnectDelay = 1000;
          this._watchdog();
          parser.feed(decoder.decode(value, {stream: true}));
        }
      } catch (error) {
        if (!this.isTerminated) this.handlers.onConnectionError?.(error.message);
      } finally {
        this.isConnected = false;
        clearTimeout(this.heartbeatTimer);
        await reader?.cancel().catch(() => {});
      }
      if (!this.isTerminated) {
        this.handlers.onConnectionChange?.('reconnecting');
        await new Promise(resolve => {
          this.resumeReconnect = resolve;
          this.reconnectTimer = setTimeout(resolve, this.reconnectDelay);
        });
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      }
    }
  }
}

if (typeof window !== 'undefined') { window.SSEClient = SSEClient; window.SSEParser = SSEParser; }
if (typeof module !== 'undefined') module.exports = {SSEClient, SSEParser};
