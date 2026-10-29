const {test} = require('node:test');
const assert = require('node:assert/strict');
const {SSEClient, SSEParser} = require('../dashboard/stream.js');

test('SSE parser preserves partial packets, CRLF, multiline JSON and cursor', () => {
  const events = [];
  const parser = new SSEParser(e => events.push(e));
  parser.feed('id: 12\r\nevent: progress\r\ndata: {"com');
  assert.equal(events.length, 0);
  parser.feed('pleted":1,\r\ndata: "total":50}\r\n\r\n');
  assert.equal(events.length, 1);
  assert.equal(events[0].id, '12');
  assert.deepEqual(JSON.parse(events[0].data), {completed:1,total:50});
});

test('stream uses API header, replays with Last-Event-ID and terminates on done', async () => {
  const original = global.fetch;
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({url, options});
    const body = requests.length === 1 ? 'id: 1\nevent: progress\ndata: {"completed":1}\n\n' : 'id: 2\nevent: done\ndata: {"completed":50}\n\n';
    return new Response(body, {status:200});
  };
  try {
    const events = [];
    const client = new SSEClient('run-uuid', {onProgress:d => events.push(d), onDone:d => events.push(d)}, '/stream', () => 'test-key');
    await client.connect();
    assert.equal(requests.length, 2);
    assert.equal(requests[0].options.headers['X-API-Key'], 'test-key');
    assert.equal(requests[1].options.headers['Last-Event-ID'], '1');
    assert.equal(requests[0].url.includes('test-key'), false);
    assert.deepEqual(events.map(e => e.completed), [1,50]);
    assert.equal(client.isTerminated, true);
  } finally { global.fetch = original; }
});

test('401 stops reconnect attempts and asks for a valid key', async () => {
  const original = global.fetch;
  let attempts = 0, message = '';
  global.fetch = async () => { attempts++; return new Response('', {status:401}); };
  try {
    const client = new SSEClient('run', {onAuthError:m => message=m});
    await client.connect();
    assert.equal(attempts, 1);
    assert.match(message, /API key/);
    assert.equal(client.isTerminated, true);
  } finally { global.fetch = original; }
});
