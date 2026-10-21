<style>
    #usage_table td, #usage_table th { vertical-align: middle; }
    #usage_table .today { font-weight: bold; }
    #known_table_wrap { max-height: 22em; overflow-y: auto; }
</style>

<script>
    $(document).ready(function () {
        const base = '/api/parentalcontrol/settings/';

        mapDataToFormUI({'frm_general_settings': base + 'get'}).done(function () {
            $('.selectpicker').selectpicker('refresh');
        });

        function gridOptions(name) {
            return {
                search: base + 'search_' + name + '/',
                get: base + 'get_' + name + '/',
                set: base + 'set_' + name + '/',
                add: base + 'add_' + name + '/',
                del: base + 'del_' + name + '/',
                toggle: base + 'toggle_' + name + '/'
            };
        }
        $('#grid-groups').UIBootgrid(gridOptions('group'));
        $('#grid-devices').UIBootgrid(gridOptions('device'));
        $('#grid-bedtimes').UIBootgrid(gridOptions('bedtime'));
        $('#grid-budgets').UIBootgrid(gridOptions('budget'));

        $('#saveAct').click(function () {
            $('#saveAct_progress').addClass('fa fa-spinner fa-pulse');
            saveFormToEndpoint(base + 'set', 'frm_general_settings', function () {
                $('#saveAct_progress').removeClass('fa fa-spinner fa-pulse');
            });
        });

        $('#applyAct').click(function () {
            $('#applyAct_progress').addClass('fa fa-spinner fa-pulse');
            ajaxCall('/api/parentalcontrol/service/reconfigure', {}, function () {
                $('#applyAct_progress').removeClass('fa fa-spinner fa-pulse');
            });
        });

        /* ---------- overzicht verbruikte tijd ---------- */

        const stateLabels = {
            active:   ['label-success', 'Actief'],
            idle:     ['label-info', 'Recent actief'],
            inactive: ['label-default', 'Inactief'],
            bedtime:  ['label-warning', 'Bedtijd (geblokkeerd)'],
            exhausted: ['label-danger', 'Tijd op (geblokkeerd)'],
            unknown:  ['label-default', '-']
        };

        function fmt(sec) {
            sec = Math.round(sec);
            const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
            return h > 0 ? h + 'u ' + String(m).padStart(2, '0') + 'm' : m + 'm';
        }

        function addTime(uuid, minutes) {
            ajaxCall('/api/parentalcontrol/service/add_time', {group: uuid, minutes: minutes}, function () {
                loadUsage();
            });
        }

        function loadUsage() {
            ajaxGet('/api/parentalcontrol/service/usage', {}, function (data) {
                $('#usage_warning').toggleClass('hidden', !!data.running);
                const dates = data.dates || [];
                const today = dates[dates.length - 1];
                const past = dates.slice(0, -1).reverse();

                const head = $('<tr>')
                    .append($('<th>').text('Groep'))
                    .append($('<th>').text('Status'))
                    .append($('<th>').text('Vandaag'))
                    .append($('<th>').text('Budget'))
                    .append($('<th>').text('Resterend'))
                    .append($('<th>').text('Extra tijd'));
                past.forEach(function (d) {
                    head.append($('<th>').text(d.substr(8, 2) + '-' + d.substr(5, 2)));
                });
                $('#usage_table thead').empty().append(head);

                const body = $('#usage_table tbody').empty();
                (data.rows || []).forEach(function (row) {
                    const st = row.enabled ? (stateLabels[row.state] || stateLabels.unknown) : ['label-default', 'Uitgeschakeld'];
                    const used = row.history[today] || 0;
                    let budget = 'onbeperkt', remaining = '-';
                    if (row.limit !== null) {
                        budget = fmt(row.limit) + (row.extra > 0 ? ' (+' + fmt(row.extra) + ')' : '');
                        remaining = fmt(Math.max(0, row.limit + row.extra - used));
                    }
                    const extra = $('<td>');
                    [15, 30, 60].forEach(function (n) {
                        extra.append($('<button type="button" class="btn btn-xs btn-default" style="margin-right: 3px;">')
                            .text('+' + n + 'm')
                            .prop('disabled', !row.enabled)
                            .click(function () { addTime(row.uuid, n); }));
                    });
                    const tr = $('<tr>')
                        .append($('<td>').text(row.name))
                        .append($('<td>').append($('<span class="label">').addClass(st[0]).text(st[1])))
                        .append($('<td class="today">').text(fmt(used)))
                        .append($('<td>').text(budget))
                        .append($('<td>').text(remaining))
                        .append(extra);
                    past.forEach(function (d) {
                        tr.append($('<td>').text(fmt(row.history[d] || 0)));
                    });
                    body.append(tr);
                });
                if (!(data.rows || []).length) {
                    body.append($('<tr>').append($('<td colspan="6">').text('Nog geen groepen aangemaakt.')));
                }
            });
        }
        /* ---------- verzoeken om extra tijd ---------- */

        const reqLabels = {
            pending:  ['label-warning', 'Wacht op beslissing'],
            approved: ['label-success', 'Goedgekeurd'],
            denied:   ['label-danger', 'Afgewezen'],
            expired:  ['label-default', 'Verlopen']
        };

        function decide(id, decision) {
            ajaxCall('/api/parentalcontrol/service/resolve_request', {id: id, decision: decision}, function () {
                setTimeout(function () { loadRequests(); loadUsage(); }, 1500);
            });
        }

        function loadRequests() {
            ajaxGet('/api/parentalcontrol/service/requests', {}, function (data) {
                const body = $('#req_table tbody').empty();
                const rows = data.rows || [];
                const pending = rows.filter(function (r) { return r.status === 'pending'; }).length;
                $('#req_badge').text(pending).toggleClass('hidden', pending === 0);
                rows.forEach(function (r) {
                    const st = reqLabels[r.status] || ['label-default', r.status];
                    const when = new Date(r.created * 1000).toLocaleString('nl-NL', {
                        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
                    const actions = $('<td>');
                    if (r.status === 'pending') {
                        actions.append($('<button type="button" class="btn btn-xs btn-success" style="margin-right: 4px;">')
                            .text('Goedkeuren').click(function () { decide(r.id, 'approve'); }));
                        actions.append($('<button type="button" class="btn btn-xs btn-danger">')
                            .text('Afwijzen').click(function () { decide(r.id, 'deny'); }));
                    }
                    body.append($('<tr>')
                        .append($('<td>').text(when))
                        .append($('<td>').text(r.name))
                        .append($('<td>').text(r.minutes + ' min'))
                        .append($('<td>').append($('<span class="label">').addClass(st[0]).text(st[1])))
                        .append(actions));
                });
                if (!rows.length) {
                    body.append($('<tr>').append($('<td colspan="5">').text('Geen verzoeken.')));
                }
            });
        }

        $('#testNotifyAct').click(function () {
            ajaxCall('/api/parentalcontrol/service/test_notification', {}, function (data) {
                stdDialogInform('Testmelding',
                    data.status === 'ok' ? 'Testmelding verstuurd.' : (data.message || 'Versturen mislukt.'),
                    'Sluiten');
            });
        });

        loadUsage();
        loadRequests();
        setInterval(function () {
            if ($('#overview').hasClass('active')) { loadUsage(); loadRequests(); }
        }, 10000);
        $('a[href="#overview"]').on('shown.bs.tab', function () { loadUsage(); loadRequests(); });

        /* ---------- bekende apparaten toevoegen ---------- */

        $('#addKnownBtn').click(function () {
            $('#known_msg').addClass('hidden');
            $('#known_table tbody').empty().append(
                $('<tr>').append($('<td colspan="5">').text('Laden...')));
            $('#known_group').empty();
            $('#DialogKnownDevices').modal('show');

            ajaxCall(base + 'search_group/', {current: 1, rowCount: 500}, function (data) {
                (data.rows || []).forEach(function (g) {
                    $('#known_group').append($('<option>').val(g.uuid).text(g.name));
                });
                $('#known_group').selectpicker('refresh');
            });

            ajaxGet(base + 'known_devices', {}, function (data) {
                const body = $('#known_table tbody').empty();
                (data.rows || []).forEach(function (dev) {
                    const tr = $('<tr>').data('mac', dev.mac);
                    tr.append($('<td>').append($('<input type="checkbox" class="known_pick">')));
                    tr.append($('<td>').append(
                        $('<input type="text" class="form-control input-sm known_name">').val(dev.name || '')));
                    tr.append($('<td>').text(dev.mac));
                    tr.append($('<td>').text((dev.ips || []).join(', ')));
                    tr.append($('<td>').text((dev.sources || []).join(', ')));
                    body.append(tr);
                });
                if (!(data.rows || []).length) {
                    body.append($('<tr>').append($('<td colspan="5">').text('Geen bekende apparaten gevonden.')));
                }
            });
        });

        $('#known_add').click(function () {
            const devices = [];
            $('#known_table tbody tr').each(function () {
                if ($(this).find('.known_pick').is(':checked')) {
                    devices.push({mac: $(this).data('mac'), name: $(this).find('.known_name').val()});
                }
            });
            const group = $('#known_group').val();
            if (!group || devices.length === 0) {
                $('#known_msg').removeClass('hidden').text('Kies een groep en minimaal een apparaat.');
                return;
            }
            ajaxCall(base + 'add_known_devices/', {group: group, devices: devices}, function (data) {
                if (data.result === 'saved') {
                    $('#DialogKnownDevices').modal('hide');
                    $('#grid-devices').bootgrid('reload');
                } else {
                    let msg = data.message || 'Toevoegen mislukt.';
                    if (data.validations) { msg += ' ' + Object.values(data.validations).join(' '); }
                    $('#known_msg').removeClass('hidden').text(msg);
                }
            });
        });
        $('#known_select_all').change(function () {
            $('#known_table .known_pick').prop('checked', $(this).is(':checked'));
        });
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#overview">{{ lang._('Overzicht') }}</a></li>
    <li><a data-toggle="tab" href="#general">{{ lang._('Algemeen') }}</a></li>
    <li><a data-toggle="tab" href="#groups">{{ lang._('Groepen') }}</a></li>
    <li><a data-toggle="tab" href="#devices">{{ lang._('Apparaten') }}</a></li>
    <li><a data-toggle="tab" href="#bedtimes">{{ lang._('Bedtijden') }}</a></li>
    <li><a data-toggle="tab" href="#budgets">{{ lang._('Tijdbudgetten') }}</a></li>
</ul>

<div class="tab-content content-box">
    <div id="overview" class="tab-pane fade in active">
        <div class="col-md-12" style="padding-top: 1em;">
            <div id="usage_warning" class="alert alert-warning hidden">
                {{ lang._('De daemon draait niet; de getoonde status is niet actueel. Schakel de plugin in op het tabblad Algemeen en klik op Toepassen.') }}
            </div>
            <table id="usage_table" class="table table-striped table-condensed">
                <thead></thead>
                <tbody></tbody>
            </table>
            <h4 style="margin-top: 1.5em;">{{ lang._('Verzoeken om extra tijd') }} <span id="req_badge" class="badge hidden"></span></h4>
            <table id="req_table" class="table table-striped table-condensed">
                <thead>
                <tr>
                    <th>{{ lang._('Wanneer') }}</th>
                    <th>{{ lang._('Groep') }}</th>
                    <th>{{ lang._('Gevraagd') }}</th>
                    <th>{{ lang._('Status') }}</th>
                    <th>{{ lang._('Beslissing') }}</th>
                </tr>
                </thead>
                <tbody></tbody>
            </table>
            <p class="text-muted">
                {{ lang._('Verbruikte tijd is de tijd waarin minstens één apparaat van de groep netwerkverkeer had. Gelijktijdig gebruik van meerdere apparaten telt één keer. Het is een benadering van schermtijd.') }}
            </p>
        </div>
    </div>

    <div id="general" class="tab-pane fade in">
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general_settings']) }}
        <div class="col-md-12">
            <hr/>
            <button class="btn btn-primary" id="saveAct" type="button">
                <b>{{ lang._('Opslaan') }}</b> <i id="saveAct_progress"></i>
            </button>
            <button class="btn btn-default" id="testNotifyAct" type="button">
                {{ lang._('Testmelding versturen') }}
            </button>
            <span class="text-muted">{{ lang._('Gebruikt de opgeslagen instellingen: sla eerst op.') }}</span>
        </div>
    </div>

    <div id="groups" class="tab-pane fade in">
        <table id="grid-groups" class="table table-condensed table-hover table-striped" data-editDialog="DialogGroup">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Aan') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Naam') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Omschrijving') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Acties') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td colspan="3">
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-plus fa-fw"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-trash-o fa-fw"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>

    <div id="devices" class="tab-pane fade in">
        <table id="grid-devices" class="table table-condensed table-hover table-striped" data-editDialog="DialogDevice">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Aan') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Naam') }}</th>
                <th data-column-id="mac" data-type="string">{{ lang._('MAC-adres') }}</th>
                <th data-column-id="group" data-type="string">{{ lang._('Groep') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Acties') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td colspan="4">
                    <button data-action="add" type="button" class="btn btn-xs btn-primary" title="{{ lang._('Handmatig toevoegen') }}"><span class="fa fa-plus fa-fw"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-trash-o fa-fw"></span></button>
                    <button id="addKnownBtn" type="button" class="btn btn-xs btn-default"><span class="fa fa-list fa-fw"></span> {{ lang._('Bekende apparaten toevoegen') }}</button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>

    <div id="bedtimes" class="tab-pane fade in">
        <table id="grid-bedtimes" class="table table-condensed table-hover table-striped" data-editDialog="DialogBedtime">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Aan') }}</th>
                <th data-column-id="groups" data-type="string">{{ lang._('Groepen') }}</th>
                <th data-column-id="days" data-type="string">{{ lang._('Dagen') }}</th>
                <th data-column-id="start" data-type="string">{{ lang._('Begin') }}</th>
                <th data-column-id="end" data-type="string">{{ lang._('Einde') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Omschrijving') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Acties') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td colspan="6">
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-plus fa-fw"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-trash-o fa-fw"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>
    <div id="budgets" class="tab-pane fade in">
        <table id="grid-budgets" class="table table-condensed table-hover table-striped" data-editDialog="DialogBudget">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Aan') }}</th>
                <th data-column-id="groups" data-type="string">{{ lang._('Groepen') }}</th>
                <th data-column-id="days" data-type="string">{{ lang._('Dagen') }}</th>
                <th data-column-id="minutes" data-type="string">{{ lang._('Apparaattijd per dag') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Omschrijving') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Acties') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td colspan="5">
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-plus fa-fw"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-trash-o fa-fw"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>
</div>

<div class="content-box" style="padding-bottom: 1.5em; margin-top: 1em;">
    <div class="col-md-12">
        <p>{{ lang._('Wijzigingen worden pas actief nadat je ze toepast.') }}</p>
        <button class="btn btn-primary" id="applyAct" type="button">
            <b>{{ lang._('Toepassen') }}</b> <i id="applyAct_progress"></i>
        </button>
    </div>
</div>

<div class="modal fade" id="DialogKnownDevices" tabindex="-1" role="dialog">
    <div class="modal-dialog modal-lg" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal" aria-label="Close"><span aria-hidden="true">&times;</span></button>
                <h4 class="modal-title">{{ lang._('Bekende apparaten toevoegen') }}</h4>
            </div>
            <div class="modal-body">
                <p class="text-muted">{{ lang._('DHCP-reserveringen, DHCP-leases en apparaten die nu in de ARP/NDP-tabel staan. Apparaten die al in een groep zitten, worden niet getoond. Je kunt de naam aanpassen voordat je toevoegt.') }}</p>
                <div class="form-group">
                    <label for="known_group">{{ lang._('Toevoegen aan groep') }}</label>
                    <select id="known_group" class="selectpicker" data-width="100%"></select>
                </div>
                <div id="known_table_wrap">
                    <table id="known_table" class="table table-condensed table-striped">
                        <thead>
                        <tr>
                            <th style="width: 2em;"><input type="checkbox" id="known_select_all"></th>
                            <th>{{ lang._('Naam') }}</th>
                            <th>{{ lang._('MAC-adres') }}</th>
                            <th>{{ lang._('IP-adres') }}</th>
                            <th>{{ lang._('Bron') }}</th>
                        </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
                <div id="known_msg" class="alert alert-danger hidden" style="margin-top: 1em;"></div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Annuleren') }}</button>
                <button type="button" class="btn btn-primary" id="known_add">{{ lang._('Toevoegen') }}</button>
            </div>
        </div>
    </div>
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogGroup, 'id': 'DialogGroup', 'label': lang._('Groep bewerken')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogDevice, 'id': 'DialogDevice', 'label': lang._('Apparaat bewerken')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogBudget, 'id': 'DialogBudget', 'label': lang._('Tijdbudget bewerken')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogBedtime, 'id': 'DialogBedtime', 'label': lang._('Bedtijd bewerken')]) }}
