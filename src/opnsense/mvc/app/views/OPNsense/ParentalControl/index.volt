<script>
    $(document).ready(function () {
        mapDataToFormUI({'frm_general_settings': '/api/parentalcontrol/settings/get'}).done(function () {
            $('.selectpicker').selectpicker('refresh');
        });

        const base = '/api/parentalcontrol/settings/';
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
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#general">{{ lang._('Algemeen') }}</a></li>
    <li><a data-toggle="tab" href="#groups">{{ lang._('Groepen') }}</a></li>
    <li><a data-toggle="tab" href="#devices">{{ lang._('Apparaten') }}</a></li>
    <li><a data-toggle="tab" href="#bedtimes">{{ lang._('Bedtijden') }}</a></li>
</ul>

<div class="tab-content content-box">
    <div id="general" class="tab-pane fade in active">
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general_settings']) }}
        <div class="col-md-12">
            <hr/>
            <button class="btn btn-primary" id="saveAct" type="button">
                <b>{{ lang._('Opslaan') }}</b> <i id="saveAct_progress"></i>
            </button>
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
                    <button data-action="add" type="button" class="btn btn-xs btn-primary"><span class="fa fa-plus fa-fw"></span></button>
                    <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default"><span class="fa fa-trash-o fa-fw"></span></button>
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
                <th data-column-id="group" data-type="string">{{ lang._('Groep') }}</th>
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
</div>

<div class="content-box" style="padding-bottom: 1.5em; margin-top: 1em;">
    <div class="col-md-12">
        <p>{{ lang._('Wijzigingen worden pas actief nadat je ze toepast.') }}</p>
        <button class="btn btn-primary" id="applyAct" type="button">
            <b>{{ lang._('Toepassen') }}</b> <i id="applyAct_progress"></i>
        </button>
    </div>
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogGroup, 'id': 'DialogGroup', 'label': lang._('Groep bewerken')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogDevice, 'id': 'DialogDevice', 'label': lang._('Apparaat bewerken')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogBedtime, 'id': 'DialogBedtime', 'label': lang._('Bedtijd bewerken')]) }}
