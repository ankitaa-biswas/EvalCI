// evalci/dashboard/stream.js
// SSE client and utility functions for the EvalCI live dashboard.

'use strict';

// ---------------------------------------------------------------------------
// SSEClient
// ---------------------------------------------------------------------------

class SSEClient {
  constructor(runId, handlers = {}, baseUrl = '/stream') {
    this.runId = runId;
    this.handlers = handlers;
    this.baseUrl = baseUrl;
    this.eventSource = null;
    this.reconnectDelay = 1000;
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.isConnected = false;
    this.isTerminated = false;
    this.HEARTBEAT_TIMEOUT_MS = 45000;
  }

  connect() {
    if (this.isTerminated) return;
    const url = `${this.baseUrl}/${this.runId}`;
    this.eventSource = new EventSource(url);

    this.eventSource.onopen = () => {
      this.isConnected = true;
      this.reconnectDelay = 1000;
      this._startHeartbeatWatchdog();
    };

    // Named event types from the server
    ['score', 'progress', 'done', 'error', 'ping'].forEach((type) => {
      this.eventSource.addEventListener(type, (e) => this._dispatch(e, type));
    });

    // Fallback for unlabelled messages
    this.eventSource.onmessage = (e) => this._dispatch(e, 'message');

    this.eventSource.onerror = () => {
      this.isConnected = false;
      if (this.eventSource) {
        this.eventSource.close();
        this.eventSource = null;
      }
      if (!this.isTerminated) this._scheduleReconnect();
    };
  }

  disconnect() {
    this.isTerminated = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isConnected = false;
  }

  _dispatch(event, type) {
    // Reset heartbeat watchdog on any incoming event
    this._startHeartbeatWatchdog();

    if (type === 'ping') return;

    let data = {};
    try { data = JSON.parse(event.data); } catch (_) {}

    const handlerMap = {
      score: this.handlers.onScore,
      progress: this.handlers.onProgress,
      done: this.handlers.onDone,
      error: this.handlers.onError,
    };

    const handler = handlerMap[type];
    if (handler) handler(data);

    if (type === 'done' || type === 'error') {
      this.disconnect();
    }
  }

  _scheduleReconnect() {
    if (this.isTerminated) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      this.connect();
    }, this.reconnectDelay);
  }

  _startHeartbeatWatchdog() {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => {
      if (!this.isTerminated) {
        if (this.eventSource) { this.eventSource.close(); this.eventSource = null; }
        this._scheduleReconnect();
      }
    }, this.HEARTBEAT_TIMEOUT_MS);
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function formatScore(score) {
  if (score === null || score === undefined) return { pct: '—', cssClass: 'score-na' };
  const pct = (score * 100).toFixed(1) + '%';
  let cssClass = 'score-bad';
  if (score >= 0.75) cssClass = 'score-good';
  else if (score >= 0.50) cssClass = 'score-warn';
  return { pct, cssClass };
}

function getComponentBadge(component) {
  const map = {
    RETRIEVER: { label: 'Retriever', icon: '🔍', colour: '#f59e0b' },
    GENERATOR: { label: 'Generator', icon: '🤖', colour: '#ef4444' },
    BOTH:      { label: 'Both',      icon: '⚠️', colour: '#a855f7' },
    UNKNOWN:   { label: 'Unknown',   icon: '❓', colour: '#64748b' },
  };
  return map[component] || map.UNKNOWN;
}

function getSeverityClass(severity) {
  const map = { HIGH: 'severity-high', MEDIUM: 'severity-medium', LOW: 'severity-low' };
  return map[severity] || 'severity-low';
}

// Expose globally
window.SSEClient = SSEClient;
window.formatScore = formatScore;
window.getComponentBadge = getComponentBadge;
window.getSeverityClass = getSeverityClass;
