// Does York's eClass expose the *official* Moodle calendar export / subscription?
const targets = [
  ['/calendar/export.php', 'calendar export UI'],
  ['/calendar/export_execute.php', 'calendar export (ICS)'],
  ['/calendar/managesubscriptions.php', 'calendar subscriptions'],
  ['/calendar/index.php', 'calendar view'],
  ['/user/manage_tokens.php', 'manage mobile tokens'],
  ['/admin/webservice/tokens.php', 'admin tokens'],
  ['/user/profile.php', 'profile'],
];

for (const [path, label] of targets) {
  const url = 'https://eclass.yorku.ca' + path;
  try {
    const res = await fetch(url, { redirect: 'manual', headers: { 'User-Agent': 'eclass-api-recon/0.1', 'Accept-Language': 'en-CA,en;q=0.9' } });
    const loc = res.headers.get('location') || '';
    const text = await res.text();
    const isSso = loc.includes('shib.yorku.ca') || loc.includes('/idp/');
    // A 404 error page from Moodle has "Error | eClass" title and a specific body.
    const moodle404 = /<title>Error \| eClass<\/title>/i.test(text);
    let verdict;
    if (res.status === 404 && moodle404) verdict = 'NOT INSTALLED / disabled';
    else if (res.status === 404) verdict = 'blocked at proxy (404)';
    else if (isSso) verdict = 'EXISTS (login required)';
    else if (res.status >= 300 && res.status < 400) verdict = `redirect ${res.status} -> ${loc.slice(0, 60)}`;
    else if (res.status === 200) verdict = 'EXISTS (public 200)';
    else verdict = `HTTP ${res.status}`;
    console.log(`${path.padEnd(34)} ${String(res.status).padEnd(4)} ${verdict.padEnd(28)} ${label}`);
  } catch (e) {
    console.log(`${path.padEnd(34)} ERR ${e.message}`);
  }
}
