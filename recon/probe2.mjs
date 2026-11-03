const J = { 'Content-Type': 'application/json' };

async function hit(label, url, opts = {}) {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 25000);
    const res = await fetch(url, {
      redirect: 'manual',
      signal: ctl.signal,
      headers: { 'User-Agent': 'eclass-api-recon/0.1 (+research)', ...(opts.headers || {}) },
      ...opts,
    });
    clearTimeout(t);
    const text = await res.text();
    console.log(`\n=== ${label}`);
    console.log(`  ${res.status} ${res.statusText} ct=${res.headers.get('content-type')}`);
    const loc = res.headers.get('location');
    if (loc) console.log('  location:', decodeURIComponent(loc).slice(0, 300));
    console.log('  body:', text.replace(/\s+/g, ' ').slice(0, 700));
    return { res, text };
  } catch (e) {
    console.log(`\n=== ${label}\n  ERR ${e.name}: ${e.message}`);
  }
}

// 1. Mobile app SSO launch endpoints
await hit('launch.php', 'https://eclass.yorku.ca/admin/tool/mobile/launch.php');
await hit('launch.php?service', 'https://eclass.yorku.ca/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=abc&urlscheme=x');

// 2. REST WS in JSON mode, with a bootstrap-ish function
await hit('get_site_info REST json',
  'https://eclass.yorku.ca/webservice/rest/server.php?moodlewsrestformat=json&wsfunction=core_webservice_get_site_info&wstoken=bogus');
await hit('tool_mobile_get_public_config',
  'https://eclass.yorku.ca/webservice/rest/server.php?moodlewsrestformat=json&wsfunction=tool_mobile_get_public_config');

// 3. Internal AJAX API (what the web UI itself calls), unauthenticated
await hit('ajax service.php unauth', 'https://eclass.yorku.ca/lib/ajax/service.php?info=core_webservice_get_site_info', {
  method: 'POST',
  headers: J,
  body: JSON.stringify([{ index: 0, methodname: 'core_webservice_get_site_info', args: {} }]),
});
await hit('ajax core_course_get_enrolled_courses_by_timeline_classification', 'https://eclass.yorku.ca/lib/ajax/service.php?sesskey=x', {
  method: 'POST',
  headers: J,
  body: JSON.stringify([{ index: 0, methodname: 'core_course_get_enrolled_courses_by_timeline_classification', args: { offset: 0, limit: 0, classification: 'inprogress', sort: 'fullname' } }]),
});

// 4. Shibboleth IdP login page + metadata
const idp = await hit('shib IdP login page', 'https://shib.yorku.ca/idp/profile/SAML2/Redirect/SSO');
if (idp?.text) {
  const forms = idp.text.match(/<form[\s\S]{0,400}?>/gi) || [];
  console.log('  FORMS:', forms.map(f => f.replace(/\s+/g, ' ').slice(0, 300)));
  const inputs = idp.text.match(/<input[^>]*>/gi) || [];
  console.log('  INPUTS:', inputs.map(f => f.replace(/\s+/g, ' ').slice(0, 200)));
}

// 5. Does York expose discovery / any manual login?
await hit('login/index.php?auth=manual', 'https://eclass.yorku.ca/login/index.php?auth=manual');
await hit('auth/shibboleth/index.php', 'https://eclass.yorku.ca/auth/shibboleth/index.php');
