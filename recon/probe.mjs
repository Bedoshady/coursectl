const targets = [
  ['GET', 'https://eclass.yorku.ca/'],
  ['GET', 'https://eclass.yorku.ca/login/index.php'],
  ['GET', 'https://eclass.yorku.ca/login/token.php'],
  ['POST', 'https://eclass.yorku.ca/login/token.php'],
  ['GET', 'https://eclass.yorku.ca/admin/tool/mobile/check.php'],
  ['POST', 'https://eclass.yorku.ca/admin/tool/mobile/check.php'],
  ['GET', 'https://eclass.yorku.ca/webservice/rest/server.php'],
  ['GET', 'https://eclass.yorku.ca/lib/ajax/service.php'],
  ['GET', 'https://eclass.yorku.ca/completion/ping.php'],
  ['GET', 'https://eclass.yorku.ca/robots.txt'],
];

for (const [method, url] of targets) {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 25000);
    const res = await fetch(url, {
      method,
      redirect: 'manual',
      signal: ctl.signal,
      headers: { 'User-Agent': 'eclass-api-recon/0.1 (+research)' },
      ...(method === 'POST'
        ? { body: new URLSearchParams({ username: 'x', password: 'y', service: 'moodle_mobile_app' }) }
        : {}),
    });
    clearTimeout(t);
    const text = await res.text();
    console.log(`\n=== ${method} ${url}`);
    console.log('status:', res.status, res.statusText);
    const interesting = ['location', 'content-type', 'server', 'set-cookie', 'x-powered-by'];
    for (const [k, v] of res.headers) {
      if (interesting.includes(k.toLowerCase())) console.log(`  ${k}: ${v.slice(0, 200)}`);
    }
    console.log('  body:', text.replace(/\s+/g, ' ').slice(0, 500));
  } catch (e) {
    console.log(`\n=== ${method} ${url}\n  ERR ${e.name}: ${e.message}`);
  }
}
