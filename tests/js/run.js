// Draait het script van index.volt in jsdom met stubs voor opnsense.js, bootstrap en bootgrid.
// Controleert dat "Bekende apparaten toevoegen" werkt, ook als bootgrid de tfoot-knoppen later
// verplaatst of kloont (zonder handlers).
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const VOLT = path.join(__dirname, '..', '..', 'src', 'opnsense', 'mvc', 'app', 'views', 'OPNsense',
  'ParentalControl', 'index.volt');
const source = fs.readFileSync(VOLT, 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
const markup = source.replace(/<script>[\s\S]*?<\/script>/, '').replace(/<style>[\s\S]*?<\/style>/, '')
  .replace(/\{\{\s*partial\([^}]*\)\s*\}\}/g, '').replace(/\{\{\s*lang\._\('([^']*)'\)\s*\}\}/g, '$1');

const CANNED = {
  '/api/parentalcontrol/settings/known_devices': {rows: [
    {mac: '00:11:22:33:44:55', name: 'i', ips: ['192.168.68.130'], sources: ['reservering']},
    {mac: '14:14:16:38:44:d8', name: 'tv_1', ips: ['192.168.68.122'], sources: ['reservering']}]},
  '/api/parentalcontrol/settings/search_group/': {rows: [{uuid: 'g1', name: 'Kids'}]},
  '/api/parentalcontrol/service/usage': {running: true, dates: ['2026-10-20', '2026-10-21'], rows: []},
  '/api/parentalcontrol/service/requests': {rows: []},
  '/api/parentalcontrol/settings/add_known_devices/': {result: 'saved', added: 1},
};

function runScenario(mode) {
  return new Promise((resolve) => {
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
    $.fn.UIBootgrid = function () {
      if (mode === 'relocate') { relocate($(this)); }
      if (mode === 'late-relocate') { const t = $(this); setTimeout(() => relocate(t), 5); }
      return this;
    };
    w.mapDataToFormUI = () => $.Deferred().resolve();
    w.saveFormToEndpoint = () => {};
    w.stdDialogInform = () => {};
    w.ajaxGet = (url, data, cb) => { calls.push('GET ' + url); cb(CANNED[url] || {}); };
    w.ajaxCall = (url, data, cb) => { calls.push('POST ' + url + ' ' + JSON.stringify(data)); cb(CANNED[url] || {}); };
    w.eval('(function(){' + script.replace('$(document).ready(function () {', 'jQuery(function () {') + '})()');
    setTimeout(() => {
      $('#addKnownBtn').trigger('click');
      assert(calls.includes('modal:show'), mode + ': dialoog opent niet');
      assert.strictEqual($('#known_table tbody tr').length, 2, mode + ': apparaten niet getoond');
      assert.strictEqual($('#known_group option').length, 1, mode + ': groepen niet getoond');
      $('#known_table .known_pick').first().prop('checked', true);
      $('#known_group').val('g1');
      $('#known_add').trigger('click');
      const post = calls.find((c) => c.startsWith('POST /api/parentalcontrol/settings/add_known_devices/'));
      assert(post, mode + ': geen add_known_devices-aanroep');
      assert(post.includes('"mac":"00:11:22:33:44:55"') && post.includes('"group":"g1"'), mode + ': verkeerde data');
      console.log('ok  ' + mode);
      w.close();
      resolve();
    }, 60);
  });
}

(async () => {
  for (const mode of ['plain', 'relocate', 'late-relocate']) { await runScenario(mode); }
  process.exit(0);
})().catch((err) => { console.error('FAIL', err.message); process.exit(1); });
