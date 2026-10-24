<script>
    $(document).ready(function () {
        const base = "/api/parentalcontrol/settings/";
        const T = {
            reservation: "{{ lang._('Reservation') }}", lease: "{{ lang._('Lease') }}", active: "{{ lang._('Active') }}",
            loading: "{{ lang._('Loading...') }}", none: "{{ lang._('No known devices found.') }}",
            pick: "{{ lang._('Choose a group and at least one device.') }}", backend: "{{ lang._('Filter backend') }}",
            testFailed: "{{ lang._('Test failed.') }}", connectionOk: "{{ lang._('Connection successful.') }}",
            testNotification: "{{ lang._('Test notification') }}", notificationSent: "{{ lang._('Test notification sent.') }}",
            sendFailed: "{{ lang._('Sending failed.') }}", addFailed: "{{ lang._('Adding failed.') }}", close: "{{ lang._('Close') }}"
        };

        mapDataToFormUI({"frm_general_settings": base + "get"}).done(function () {
            $(".selectpicker").selectpicker("refresh");
        });

        function gridOptions(name) {
            return {
                search: base + "search_" + name + "/",
                get: base + "get_" + name + "/",
                set: base + "set_" + name + "/",
                add: base + "add_" + name + "/",
                del: base + "del_" + name + "/",
                toggle: base + "toggle_" + name + "/"
            };
        }
        $("#grid-groups").UIBootgrid(gridOptions("group"));
        $("#grid-devices").UIBootgrid(gridOptions("device"));
        $("#grid-bedtimes").UIBootgrid(gridOptions("bedtime"));
        $("#grid-budgets").UIBootgrid(gridOptions("budget"));
        $("#grid-profiles").UIBootgrid(gridOptions("profile"));

        $("#saveAct").click(function () {
            $("#saveAct_progress").addClass("fa fa-spinner fa-pulse");
            saveFormToEndpoint(base + "set", "frm_general_settings", function () {
                $("#saveAct_progress").removeClass("fa fa-spinner fa-pulse");
            });
        });

        $("#applyAct").click(function () {
            $("#applyAct_progress").addClass("fa fa-spinner fa-pulse");
            ajaxCall("/api/parentalcontrol/service/reconfigure", {}, function () {
                $("#applyAct_progress").removeClass("fa fa-spinner fa-pulse");
            });
        });

        $(document).on("click", "#testBackendAct", function () {
            ajaxCall("/api/parentalcontrol/service/test_backend", {}, function (data) {
                stdDialogInform(T.backend, data.message || (data.status === "ok" ? T.connectionOk : T.testFailed), T.close);
            });
        });

        $("#testNotifyAct").click(function () {
            ajaxCall("/api/parentalcontrol/service/test_notification", {}, function (data) {
                stdDialogInform(T.testNotification, data.status === "ok" ? T.notificationSent : (data.message || T.sendFailed), T.close);
            });
        });

        /* ---------- add known devices ---------- */

        $(document).on("click", "#addKnownBtn", function () {
            $("#known_msg").addClass("hidden");
            $("#known_table tbody").empty().append($("<tr>").append($('<td colspan="5">').text(T.loading)));
            $("#known_group").empty();
            $("#DialogKnownDevices").modal("show");

            ajaxCall(base + "search_group/", {current: 1, rowCount: 500}, function (data) {
                (data.rows || []).forEach(function (g) {
                    $("#known_group").append($("<option>").val(g.uuid).text(g.name));
                });
                $("#known_group").selectpicker("refresh");
            });

            ajaxGet(base + "known_devices", {}, function (data) {
                const body = $("#known_table tbody").empty();
                (data.rows || []).forEach(function (dev) {
                    const tr = $("<tr>").data("mac", dev.mac);
                    tr.append($("<td>").append($('<input type="checkbox" class="known_pick">')));
                    tr.append($("<td>").append($('<input type="text" class="form-control input-sm known_name">').val(dev.name || "")));
                    tr.append($("<td>").text(dev.mac));
                    tr.append($("<td>").text((dev.ips || []).join(", ")));
                    tr.append($("<td>").text((dev.sources || []).map(function (s) { return T[s] || s; }).join(", ")));
                    body.append(tr);
                });
                if (!(data.rows || []).length) {
                    body.append($("<tr>").append($('<td colspan="5">').text(T.none)));
                }
            });
        });

        $(document).on("click", "#known_add", function () {
            const devices = [];
            $("#known_table tbody tr").each(function () {
                if ($(this).find(".known_pick").is(":checked")) {
                    devices.push({mac: $(this).data("mac"), name: $(this).find(".known_name").val()});
                }
            });
            const group = $("#known_group").val();
            if (!group || devices.length === 0) {
                $("#known_msg").removeClass("hidden").text(T.pick);
                return;
            }
            ajaxCall(base + "add_known_devices/", {group: group, devices: devices}, function (data) {
                if (data.result === "saved") {
                    $("#DialogKnownDevices").modal("hide");
                    $("#grid-devices").bootgrid("reload");
                } else {
                    let msg = data.message || T.addFailed;
                    if (data.validations) { msg += " " + Object.values(data.validations).join(" "); }
                    $("#known_msg").removeClass("hidden").text(msg);
                }
            });
        });
        $(document).on("change", "#known_select_all", function () {
            $("#known_table .known_pick").prop("checked", $(this).is(":checked"));
        });
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#general">{{ lang._('General') }}</a></li>
    <li><a data-toggle="tab" href="#groups">{{ lang._('Groups') }}</a></li>
    <li><a data-toggle="tab" href="#devices">{{ lang._('Devices') }}</a></li>
    <li><a data-toggle="tab" href="#bedtimes">{{ lang._('Bedtimes') }}</a></li>
    <li><a data-toggle="tab" href="#budgets">{{ lang._('Time budgets') }}</a></li>
    <li><a data-toggle="tab" href="#profiles">{{ lang._('Website profiles') }}</a></li>
</ul>

<div class="tab-content content-box">
    <div id="general" class="tab-pane fade in active">
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general_settings']) }}
        <div class="col-md-12">
            <hr/>
            <button class="btn btn-primary" id="saveAct" type="button">
                <b>{{ lang._('Save') }}</b> <i id="saveAct_progress"></i>
            </button>
            <button class="btn btn-default" id="testNotifyAct" type="button">{{ lang._('Send test notification') }}</button>
            <button class="btn btn-default" id="testBackendAct" type="button">{{ lang._('Test filter backend connection') }}</button>
            <span class="text-muted">{{ lang._('Tests use the saved settings: save first.') }}</span>
        </div>
    </div>

    <div id="groups" class="tab-pane fade in">
        <table id="grid-groups" class="table table-condensed table-hover table-striped" data-editDialog="DialogGroup">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-type="string" data-width="6em" data-formatter="rowtoggle">{{ lang._('Enabled') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Name') }}</th>
                <th data-column-id="profile" data-type="string">{{ lang._('Website profile') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Description') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
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

    <div id="devices" class="tab-pane fade in">
        <div class="col-md-12" style="padding: 0.8em 0 0.4em 0;">
            <button id="addKnownBtn" type="button" class="btn btn-sm btn-default">
                <span class="fa fa-list fa-fw"></span> {{ lang._('Add known devices') }}
            </button>
        </div>
        <table id="grid-devices" class="table table-condensed table-hover table-striped" data-editDialog="DialogDevice">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-type="string" data-width="6em" data-formatter="rowtoggle">{{ lang._('Enabled') }}</th>
                <th data-column-id="name" data-type="string">{{ lang._('Name') }}</th>
                <th data-column-id="mac" data-type="string">{{ lang._('MAC address') }}</th>
                <th data-column-id="group" data-type="string">{{ lang._('Group') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
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

    <div id="bedtimes" class="tab-pane fade in">
        <table id="grid-bedtimes" class="table table-condensed table-hover table-striped" data-editDialog="DialogBedtime">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="enabled" data-type="string" data-width="6em" data-formatter="rowtoggle">{{ lang._('Enabled') }}</th>
                <th data-column-id="groups" data-type="string">{{ lang._('Groups') }}</th>
                <th data-column-id="days" data-type="string">{{ lang._('Days') }}</th>
                <th data-column-id="start" data-type="string">{{ lang._('Start') }}</th>
                <th data-column-id="end" data-type="string">{{ lang._('End') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Description') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td colspan="7">
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
                <th data-column-id="enabled" data-type="string" data-width="6em" data-formatter="rowtoggle">{{ lang._('Enabled') }}</th>
                <th data-column-id="groups" data-type="string">{{ lang._('Groups') }}</th>
                <th data-column-id="days" data-type="string">{{ lang._('Days') }}</th>
                <th data-column-id="minutes" data-type="string">{{ lang._('Device time per day') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Description') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
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

    <div id="profiles" class="tab-pane fade in">
        <table id="grid-profiles" class="table table-condensed table-hover table-striped" data-editDialog="DialogProfile">
            <thead>
            <tr>
                <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">ID</th>
                <th data-column-id="name" data-type="string">{{ lang._('Name') }}</th>
                <th data-column-id="safesearch" data-type="string" data-width="9em" data-formatter="boolean">{{ lang._('SafeSearch') }}</th>
                <th data-column-id="parental" data-type="string" data-width="9em" data-formatter="boolean">{{ lang._('Adult filter') }}</th>
                <th data-column-id="categories" data-type="string">{{ lang._('Blocked categories') }}</th>
                <th data-column-id="description" data-type="string">{{ lang._('Description') }}</th>
                <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
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
        <p>{{ lang._('Changes only take effect after you apply them.') }}</p>
        <button class="btn btn-primary" id="applyAct" type="button">
            <b>{{ lang._('Apply') }}</b> <i id="applyAct_progress"></i>
        </button>
    </div>
</div>

<div class="modal fade" id="DialogKnownDevices" tabindex="-1" role="dialog">
    <div class="modal-dialog modal-lg" role="document">
        <div class="modal-content">
            <div class="modal-header">
                <button type="button" class="close" data-dismiss="modal" aria-label="Close"><span aria-hidden="true">&times;</span></button>
                <h4 class="modal-title">{{ lang._('Add known devices') }}</h4>
            </div>
            <div class="modal-body">
                <p class="text-muted">{{ lang._('DHCP reservations, DHCP leases and devices currently in the ARP/NDP table. Devices that are already in a group are not shown. You can change the name before adding. This name is stored for this plugin only and has no relation to the name in the DHCP table.') }}</p>
                <div class="form-group">
                    <label for="known_group">{{ lang._('Add to group') }}</label>
                    <select id="known_group" class="selectpicker" data-width="100%"></select>
                </div>
                <div id="known_table_wrap" style="max-height: 22em; overflow-y: auto;">
                    <table id="known_table" class="table table-condensed table-striped">
                        <thead>
                        <tr>
                            <th style="width: 2em;"><input type="checkbox" id="known_select_all"></th>
                            <th>{{ lang._('Name') }}</th>
                            <th>{{ lang._('MAC address') }}</th>
                            <th>{{ lang._('IP address') }}</th>
                            <th>{{ lang._('Source') }}</th>
                        </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
                <div id="known_msg" class="alert alert-danger hidden" style="margin-top: 1em;"></div>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn btn-default" data-dismiss="modal">{{ lang._('Cancel') }}</button>
                <button type="button" class="btn btn-primary" id="known_add">{{ lang._('Add') }}</button>
            </div>
        </div>
    </div>
</div>

{{ partial("layout_partials/base_dialog", ['fields': formDialogGroup, 'id': 'DialogGroup', 'label': lang._('Edit group')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogDevice, 'id': 'DialogDevice', 'label': lang._('Edit device')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogBedtime, 'id': 'DialogBedtime', 'label': lang._('Edit bedtime')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogBudget, 'id': 'DialogBudget', 'label': lang._('Edit time budget')]) }}
{{ partial("layout_partials/base_dialog", ['fields': formDialogProfile, 'id': 'DialogProfile', 'label': lang._('Edit website profile')]) }}
