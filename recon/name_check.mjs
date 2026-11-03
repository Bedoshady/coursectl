// Check PyPI + npm availability for candidate project names.
const candidates = [
  'coursectl', 'unipipe', 'lectern', 'termctl', 'campuspipe', 'classync',
  'coursemcp', 'unimcp', 'notely', 'termfeed', 'coursepipe', 'campusfeed',
  'eclass-unofficial-api', 'eclass_unofficial_api', 'eclass-api',
  'moodlectl', 'moodle-cli',
];

async function check(name) {
  const out = { name, pypi: '?', npm: '?' };
  try {
    const r = await fetch(`https://pypi.org/pypi/${encodeURIComponent(name)}/json`, { redirect: 'manual' });
    out.pypi = r.status === 404 ? 'FREE' : r.status === 200 ? 'taken' : `http ${r.status}`;
  } catch { out.pypi = 'err'; }
  try {
    const r = await fetch(`https://registry.npmjs.org/${encodeURIComponent(name)}`, { redirect: 'manual' });
    out.npm = r.status === 404 ? 'FREE' : r.status === 200 ? 'taken' : `http ${r.status}`;
  } catch { out.npm = 'err'; }
  return out;
}

const results = await Promise.all(candidates.map(check));
console.log('NAME'.padEnd(26) + 'PyPI'.padEnd(10) + 'npm');
console.log('-'.repeat(46));
for (const r of results) {
  const flag = r.pypi === 'FREE' ? '  <- free on pypi' : '';
  console.log(r.name.padEnd(26) + String(r.pypi).padEnd(10) + String(r.npm) + flag);
}
