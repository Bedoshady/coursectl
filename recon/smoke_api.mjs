// Smoke-test the running HTTP server.
const base = process.argv[2] || 'http://127.0.0.1:8788';

async function hit(method, path, { headers = {}, body } = {}) {
  const res = await fetch(base + path, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = text.slice(0, 300); }
  console.log(`${method} ${path} -> ${res.status}`);
  console.log('   ', typeof json === 'string' ? json : JSON.stringify(json).slice(0, 260));
  return { res, json };
}

await hit('GET', '/health');
await hit('GET', '/');
await hit('GET', '/v1/courses');
await hit('GET', '/v1/courses', { headers: { Authorization: 'Bearer ck_bogus' } });
await hit('GET', '/v1/admin/stats');
await hit('POST', '/v1/auth/login', { body: { username: 'x', password: 'y', strategy: 'nope' } });
const openapi = await fetch(base + '/openapi.json');
const spec = await openapi.json();
console.log('\nOpenAPI paths:', Object.keys(spec.paths).length);
for (const [p, ops] of Object.entries(spec.paths)) {
  console.log(`  ${Object.keys(ops).join(',').toUpperCase().padEnd(6)} ${p}`);
}
