// Runs the page scripts of the Volt views in jsdom with stubs for opnsense.js, bootstrap and bootgrid.
//  * Administration: "Add known devices" must work, even when bootgrid moves/clones the tfoot buttons later.
//  * Overview: group rows are clickable and open a detail panel; day controls call the API.
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const VIEWS = path.join(__dirname, '..', '..', 'src', 'opnsense', 'mvc', 'app', 'views', 'OPNsense', 'ParentalControl');
const langStrings = (text) => text.replace(/\{\{\s*lang\._\('((?:[^'\\]|\\.)*)'\)\s*\}\}/g, (m, t) => t.replace(/\\'/g, "'"));

function load(file) {
  const source = fs.readFileSync(path.join(VIEWS, file), 'utf8');
  const script = langStrings(source.match(/<script>([\s\S]*?)<\/script>/)[1]);
  const markup = langStrings(source.replace(/<script>[\s\S]*?<\/script>/, '').replace(/<style>[\s\S]*?<\/style>/, '')
    .replace(/\{\{\s*partial\([^}]*\)\s*\}\}/g, ''));
  return {script, markup};
}

function boot(file, canned, mode) {
  const {script, markup} = load(file);
  const dom = new JSDOM('<body>' + markup + '</body>', {runScripts: 'outside-only'});
  const w = dom.window;
  const $ = require('jquery')(w);
  w.$ = w.jQuery = $;
  const calls = [];
  const relocate = (table) => {
    const bar = $('<div class="actionBar"></div>').insertBefore(table);
    table.find('tfoot button').each(function () { bar.append($(this).clone()); });
    table.find('tfoot').remove();
  };
  $.fn.modal = function (arg) { calls.push('modal:' + arg); return this; };
  $.fn.selectpicker = function () { return this; };
  $.fn.bootgrid = function () { return this; };
  $.fn.animate = function () { return this; };
  $.fn.UIBootgrid = function () {
    if (mode === 'relocate') { relocate($(this)); }
    if (mode === 'late-relocate') { const t = $(this); setTimeout(() => relocate(t), 5); }
    return this;
  };
  w.mapDataToFormUI = () => $.Deferred().resolve();
  w.saveFormToEndpoint = (url, form, ok) => { calls.push('SAVE ' + url + ' ' + form); ok(); };
  w.stdDialogInform = () => {};
  w.stdDialogConfirm = (title, message, yes, no, cb) => { calls.push('confirm:' + title); cb(); };
  w.ajaxGet = (url, data, cb) => { calls.push('GET ' + url + ' ' + JSON.stringify(data)); cb(canned[url] || {}); };
  w.ajaxCall = (url, data, cb) => { calls.push('POST ' + url + ' ' + JSON.stringify(data)); cb(canned[url] || {}); };
  w.eval('(function(){' + script.replace('$(document).ready(function () {', 'jQuery(function () {') + '})()');
  return {w, $, calls};
}

const ADMIN = {
  '/api/parentalcontrol/settings/known_devices': {rows: [
    {mac: '00:11:22:33:44:55', name: 'i', ips: ['192.168.68.130'], sources: ['reservation']},
    {mac: '14:14:16:38:44:d8', name: 'tv_1', ips: ['192.168.68.122'], sources: ['reservation', 'active']}]},
  '/api/parentalcontrol/settings/search_group/': {rows: [{uuid: 'g1', name: 'Kids'}]},
  '/api/parentalcontrol/settings/add_known_devices/': {result: 'saved', added: 1},
};

function adminScenario(mode) {
  return new Promise((resolve) => {
    const {w, $, calls} = boot('administration.volt', ADMIN, mode);
    setTimeout(() => {
      $('#addKnownBtn').trigger('click');
      assert(calls.includes('modal:show'), mode + ': dialog does not open');
      assert.strictEqual($('#known_table tbody tr').length, 2, mode + ': devices not shown');
      assert.strictEqual($('#known_group option').length, 1, mode + ': groups not shown');
      assert(/Reservation, Active/.test($('#known_table tbody tr').eq(1).text()), mode + ': source labels missing');
      $('#known_table .known_pick').first().prop('checked', true);
      $('#known_group').val('g1');
      $('#known_add').trigger('click');
      const post = calls.find((c) => c.startsWith('POST /api/parentalcontrol/settings/add_known_devices/'));
      assert(post, mode + ': no add_known_devices call');
      assert(post.includes('"mac":"00:11:22:33:44:55"') && post.includes('"group":"g1"'), mode + ': wrong data');
      if (mode === 'plain') {
        calls.length = 0;
        $('#saveAct').trigger('click');
        assert(calls.includes('SAVE /api/parentalcontrol/settings/set frm_general_settings'), 'Save did not post the General form');
        calls.length = 0;
        $('#applyAct').trigger('click');
        const saveAt = calls.findIndex((c) => c.startsWith('SAVE '));
        const applyAt = calls.findIndex((c) => c.startsWith('POST /api/parentalcontrol/service/reconfigure'));
        assert(saveAt >= 0 && applyAt > saveAt, 'Apply must save the General tab first, then reconfigure: ' + calls.join(' | '));
        $('#cleanupBackendAct').trigger('click');
        assert(calls.includes('confirm:Remove items from filter backend'), 'cleanup was not confirmed');
        assert(calls.some((c) => c.startsWith('POST /api/parentalcontrol/service/cleanup_backend')), 'cleanup not sent');
      }
      console.log('ok  administration ' + mode);
      w.close();
      resolve();
    }, 60);
  });
}

const OVERVIEW = {
  '/api/parentalcontrol/service/usage': {running: true, dates: ['2026-10-20', '2026-10-21'], backend: {state: 'ok', message: 'AdGuard Home v0.107', warnings: []},
    rows: [{uuid: 'g1', name: 'Kids', enabled: true, state: 'exhausted', limit: 3600, extra: 0, override: null,
            history: {'2026-10-20': 100, '2026-10-21': 3600}}]},
  '/api/parentalcontrol/service/requests': {rows: [{id: 'abc123', name: 'Kids', minutes: 30, status: 'pending', created: 1760000000}]},
  '/api/parentalcontrol/service/stats': {status: 'ok', total: 3700, usage: [['2026-10-20', 100], ['2026-10-21', 3600]],
    apps: [{name: 'YouTube', count: 5}], sites: [{domain: 'youtube.com', count: 7, blocked: 0}, {domain: 'ads.net', count: 2, blocked: 2}],
    blocks: [{start: 1760000000, end: null, duration: 60, reason: 'bedtime'}],
    events: [{ts: 1760000100, kind: 'override', mode: 'block'}, {ts: 1760000200, kind: 'request_resolved', status: 'approved', minutes: 30}],
    queries: [{ts: 1760000300, domain: 'tiktok.com', blocked: true}], queries_available: true, truncated: false, queries_note: ''},
};

function overviewScenario() {
  return new Promise((resolve) => {
    const {w, $, calls} = boot('overview.volt', OVERVIEW, 'plain');
    setTimeout(() => {
      assert(/Time is up \(blocked\)/.test($('#usage_table tbody').text()), 'status label missing');
      assert(!$('#backend_status').hasClass('hidden'), 'backend status hidden');
      assert($('#req_table .btn-success').length === 1, 'request buttons missing');
      assert($('#group_detail').hasClass('hidden'), 'detail panel should start hidden');

      $('.group-link').first().trigger('click');
      assert(!$('#group_detail').hasClass('hidden'), 'detail panel did not open');
      assert(/Kids/.test($('#detail_title').text()), 'detail title wrong');
      assert(calls.some((c) => c.startsWith('GET /api/parentalcontrol/service/stats') && c.includes('"period":"day"')), 'stats not requested');
      assert.strictEqual($('#apps_table tbody tr').length, 1, 'apps not shown');
      assert.strictEqual($('#sites_table tbody tr').length, 2, 'sites not shown');
      assert.strictEqual($('#blocks_table tbody tr').length, 1, 'block history not shown');
      const activity = $('#activity_table tbody').text();
      assert(/Visited|Blocked: tiktok\.com/.test(activity) && /Day control: Block for today/.test(activity) &&
             /Request Approved \(30 minutes\)/.test(activity), 'activity wrong: ' + activity);

      $('#period_buttons button[data-period="week"]').trigger('click');
      assert(calls.some((c) => c.startsWith('GET /api/parentalcontrol/service/stats') && c.includes('"period":"week"')), 'period change ignored');

      $('.dropdown-menu a:contains("Open for today")').first().trigger('click');
      assert(calls.some((c) => c.includes('group_command') && c.includes('"command":"open_day"')), 'open day not sent');
      $('.dropdown-menu a:contains("Block for today")').first().trigger('click');
      assert(calls.includes('confirm:Block for today') && calls.some((c) => c.includes('"command":"block_day"')), 'block day not confirmed/sent');
      $('.dropdown-menu a:contains("Reset the counters of today")').first().trigger('click');
      assert(calls.some((c) => c.includes('"command":"reset_counters"')), 'reset not sent');
      $('.dropdown-menu a:contains("Add 30 minutes")').first().trigger('click');
      assert(calls.some((c) => c.includes('add_time') && c.includes('"minutes":30')), 'add time not sent');
      $('#emergencyAct').trigger('click');
      assert(calls.includes('confirm:Emergency stop'), 'emergency stop was not confirmed');
      assert(calls.some((c) => c.startsWith('POST /api/parentalcontrol/service/emergency')), 'emergency stop not sent');
      console.log('ok  overview');
      w.close();
      resolve();
    }, 80);
  });
}

(async () => {
  for (const mode of ['plain', 'relocate', 'late-relocate']) { await adminScenario(mode); }
  await overviewScenario();
  process.exit(0);
})().catch((err) => { console.error('FAIL', err.message); process.exit(1); });
