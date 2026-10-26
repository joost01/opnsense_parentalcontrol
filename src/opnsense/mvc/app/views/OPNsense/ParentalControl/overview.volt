<style>
    #usage_table td, #usage_table th { vertical-align: middle; }
    #usage_table .today { font-weight: bold; }
    .group-link { cursor: pointer; font-weight: bold; }
    #group_detail h4 { margin-top: 1.2em; }
    #group_detail .progress { margin: 0; height: 14px; }
</style>

<script>
    $(document).ready(function () {
        const svc = "/api/parentalcontrol/service/";
        const T = {
            active: "{{ lang._('Active') }}", idle: "{{ lang._('Recently active') }}", inactive: "{{ lang._('Inactive') }}", bedtime: "{{ lang._('Bedtime (blocked)') }}",
            exhausted: "{{ lang._('Time is up (blocked)') }}", manual: "{{ lang._('Blocked by a parent') }}", disabled: "{{ lang._('Disabled') }}",
            openDay: "{{ lang._('Open all day') }}", unlimited: "{{ lang._('unlimited') }}",
            group: "{{ lang._('Group') }}", status: "{{ lang._('Status') }}", today: "{{ lang._('Today') }}", budget: "{{ lang._('Budget') }}", remaining: "{{ lang._('Remaining') }}",
            actions: "{{ lang._('Actions') }}", none: "{{ lang._('No groups created yet.') }}",
            addMinutes: "{{ lang._('Add %d minutes') }}", blockDay: "{{ lang._('Block for today') }}", openDayCmd: "{{ lang._('Open for today') }}",
            normalDay: "{{ lang._('Back to normal') }}", resetCounters: "{{ lang._('Reset the counters of today') }}",
            confirmReset: "{{ lang._('Reset the time used and the extra time of today for this group?') }}",
            confirmBlock: "{{ lang._('Block internet for this group for the rest of the day?') }}",
            yes: "{{ lang._('Yes') }}", no: "{{ lang._('No') }}",
            pending: "{{ lang._('Waiting for a decision') }}", approved: "{{ lang._('Approved') }}", rejected: "{{ lang._('Rejected') }}", expired: "{{ lang._('Expired') }}",
            approve: "{{ lang._('Approve') }}", reject: "{{ lang._('Reject') }}", noRequests: "{{ lang._('No requests.') }}", minutesShort: "{{ lang._('%d min') }}",
            blocked: "{{ lang._('Blocked') }}", released: "{{ lang._('Released') }}", requested: "{{ lang._('Requested %d minutes') }}",
            requestResolved: "{{ lang._('Request %s (%d minutes)') }}", timeAdded: "{{ lang._('A parent added %d minutes') }}",
            dayControl: "{{ lang._('Day control') }}", countersReset: "{{ lang._('Counters reset') }}", visited: "{{ lang._('Visited %s') }}", blockedQuery: "{{ lang._('Blocked: %s') }}",
            backend: "{{ lang._('Filter backend') }}", okLabel: "{{ lang._('OK') }}", pendingLabel: "{{ lang._('busy') }}", errorLabel: "{{ lang._('error') }}",
            details: "{{ lang._('Details for %s') }}", total: "{{ lang._('Total time in this period') }}", noData: "{{ lang._('No data in this period.') }}",
            truncated: "{{ lang._('Based on the most recent queries only.') }}", ongoing: "{{ lang._('ongoing') }}",
            emergency: "{{ lang._('Emergency stop') }}", close: "{{ lang._('Close') }}",
            confirmEmergency: "{{ lang._('This disables parental control, releases all blocked devices and stops the daemon. Your settings are kept. Continue?') }}",
            stopped: "{{ lang._('Parental control was stopped. Enable it again on the Administration page.') }}"
        };
        const reasonLabels = {bedtime: "{{ lang._('Bedtime') }}", exhausted: "{{ lang._('Time is up') }}", manual: "{{ lang._('Blocked by a parent') }}"};
        const overrideLabels = {block: T.blockDay, open: T.openDayCmd, normal: T.normalDay};
        const stateClass = {active: "label-success", idle: "label-info", inactive: "label-default",
                            bedtime: "label-warning", exhausted: "label-danger", manual: "label-danger"};
        let selected = null;
        let period = "day";

        function fmt(sec) {
            sec = Math.round(sec);
            const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
            return h > 0 ? h + "h " + String(m).padStart(2, "0") + "m" : m + "m";
        }
        function when(ts) {
            return new Date(ts * 1000).toLocaleString(undefined, {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
        }
        function format(text) {
            const args = Array.prototype.slice.call(arguments, 1);
            return text.replace(/%[ds]/g, function () { return args.shift(); });
        }

        function command(uuid, cmd) {
            ajaxCall(svc + "group_command", {group: uuid, command: cmd}, function () {
                setTimeout(refresh, 1200);
            });
        }
        function addTime(uuid, minutes) {
            ajaxCall(svc + "add_time", {group: uuid, minutes: minutes}, function () { setTimeout(refresh, 1200); });
        }

        function actionMenu(row) {
            const menu = $('<ul class="dropdown-menu">');
            [15, 30, 60].forEach(function (n) {
                menu.append($("<li>").append($('<a href="#">').text(format(T.addMinutes, n)).click(function (e) {
                    e.preventDefault(); addTime(row.uuid, n);
                })));
            });
            menu.append('<li role="separator" class="divider"></li>');
            const items = [
                [T.blockDay, function () { stdDialogConfirm(T.blockDay, T.confirmBlock, T.yes, T.no, function () { command(row.uuid, "block_day"); }); }],
                [T.openDayCmd, function () { command(row.uuid, "open_day"); }],
                [T.normalDay, function () { command(row.uuid, "normal_day"); }]
            ];
            items.forEach(function (it) {
                menu.append($("<li>").append($('<a href="#">').text(it[0]).click(function (e) { e.preventDefault(); it[1](); })));
            });
            menu.append('<li role="separator" class="divider"></li>');
            menu.append($("<li>").append($('<a href="#">').text(T.resetCounters).click(function (e) {
                e.preventDefault();
                stdDialogConfirm(T.resetCounters, T.confirmReset, T.yes, T.no, function () { command(row.uuid, "reset_counters"); });
            })));
            return $('<div class="btn-group">')
                .append($('<button type="button" class="btn btn-xs btn-default dropdown-toggle" data-toggle="dropdown">')
                    .prop("disabled", !row.enabled).text(T.actions + " ").append('<span class="caret"></span>'))
                .append(menu);
        }

        function loadUsage() {
            ajaxGet(svc + "usage", {}, function (data) {
                $("#usage_warning").toggleClass("hidden", !!data.running);
                const be = data.backend;
                const box = $("#backend_status").addClass("hidden");
                if (be && be.state !== "disabled") {
                    const cls = {ok: "alert-success", pending: "alert-warning"}[be.state] || "alert-danger";
                    const label = {ok: T.okLabel, pending: T.pendingLabel}[be.state] || T.errorLabel;
                    box.removeClass("hidden alert-success alert-danger alert-warning").addClass(cls)
                        .text(T.backend + ": " + label + " - " + be.message +
                              ((be.warnings || []).length ? " (" + be.warnings.join("; ") + ")" : ""));
                }
                const dates = data.dates || [];
                const today = dates[dates.length - 1];
                const body = $("#usage_table tbody").empty();
                (data.rows || []).forEach(function (row) {
                    const used = row.history[today] || 0;
                    let budget = T.unlimited, remaining = "-";
                    if (row.limit !== null) {
                        budget = fmt(row.limit) + (row.extra > 0 ? " (+" + fmt(row.extra) + ")" : "");
                        remaining = fmt(Math.max(0, row.limit + row.extra - used));
                    }
                    const state = row.enabled ? row.state : "disabled";
                    const status = $("<td>").append($('<span class="label">')
                        .addClass(stateClass[state] || "label-default").text(T[state] || "-"));
                    if (row.enabled && row.override === "open") {
                        status.append(" ").append($('<span class="label label-info">').text(T.openDay));
                    }
                    const link = $('<a class="group-link">').text(row.name).click(function () { selectGroup(row.uuid, row.name); });
                    body.append($("<tr>").append($("<td>").append(link)).append(status)
                        .append($('<td class="today">').text(fmt(used))).append($("<td>").text(budget))
                        .append($("<td>").text(remaining)).append($("<td>").append(actionMenu(row))));
                });
                if (!(data.rows || []).length) {
                    body.append($("<tr>").append($('<td colspan="6">').text(T.none)));
                }
            });
        }

        function loadRequests() {
            ajaxGet(svc + "requests", {}, function (data) {
                const body = $("#req_table tbody").empty();
                const rows = data.rows || [];
                const pending = rows.filter(function (r) { return r.status === "pending"; }).length;
                $("#req_badge").text(pending).toggleClass("hidden", pending === 0);
                rows.forEach(function (r) {
                    const cls = {pending: "label-warning", approved: "label-success", rejected: "label-danger", denied: "label-danger"}[r.status] || "label-default";
                    const actions = $("<td>");
                    if (r.status === "pending") {
                        actions.append($('<button type="button" class="btn btn-xs btn-success" style="margin-right: 4px;">')
                            .text(T.approve).click(function () { decide(r.id, "approve"); }));
                        actions.append($('<button type="button" class="btn btn-xs btn-danger">')
                            .text(T.reject).click(function () { decide(r.id, "deny"); }));
                    }
                    body.append($("<tr>").append($("<td>").text(when(r.created))).append($("<td>").text(r.name))
                        .append($("<td>").text(format(T.minutesShort, r.minutes)))
                        .append($("<td>").append($('<span class="label">').addClass(cls)
                            .text(T[r.status === "denied" ? "rejected" : r.status] || r.status)))
                        .append(actions));
                });
                if (!rows.length) {
                    body.append($("<tr>").append($('<td colspan="5">').text(T.noRequests)));
                }
            });
        }
        function decide(id, decision) {
            ajaxCall(svc + "resolve_request", {id: id, decision: decision}, function () {
                setTimeout(refresh, 1500);
            });
        }

        /* ---------- group details ---------- */

        function selectGroup(uuid, name) {
            selected = {uuid: uuid, name: name};
            $("#group_detail").removeClass("hidden");
            $("#detail_title").text(format(T.details, name));
            $("html, body").animate({scrollTop: $("#group_detail").offset().top - 80}, 200);
            loadStats();
        }
        $(document).on("click", "#period_buttons button", function () {
            period = $(this).data("period");
            $("#period_buttons button").removeClass("active");
            $(this).addClass("active");
            loadStats();
        });

        function fillTable(selector, rows, cells, emptyText, cols) {
            const body = $(selector + " tbody").empty();
            rows.forEach(function (r) {
                const tr = $("<tr>");
                cells(r).forEach(function (c) { tr.append($("<td>").append(c)); });
                body.append(tr);
            });
            if (!rows.length) { body.append($("<tr>").append($("<td>").attr("colspan", cols).text(emptyText))); }
        }

        function describe(e) {
            switch (e.kind) {
                case "blocked": return T.blocked + " (" + (reasonLabels[e.reason] || e.reason) + ")";
                case "released": return T.released;
                case "request": return format(T.requested, e.minutes);
                case "request_resolved": return format(T.requestResolved, T[e.status] || e.status, e.minutes);
                case "time_added": return format(T.timeAdded, e.minutes);
                case "override": return T.dayControl + ": " + (overrideLabels[e.mode] || e.mode);
                case "reset": return T.countersReset;
                default: return e.kind;
            }
        }

        function loadStats() {
            if (!selected) { return; }
            ajaxGet(svc + "stats", {group: selected.uuid, period: period}, function (d) {
                if (d.status !== "ok") { $("#detail_total").text(d.message || ""); return; }
                $("#detail_total").text(T.total + ": " + fmt(d.total));
                const max = Math.max.apply(null, d.usage.map(function (u) { return u[1]; }).concat([1]));
                fillTable("#usage_days", d.usage, function (u) {
                    return [u[0], fmt(u[1]), $('<div class="progress">').append(
                        $('<div class="progress-bar">').css("width", Math.round(100 * u[1] / max) + "%"))];
                }, T.noData, 3);
                const note = d.queries_available ? (d.truncated ? T.truncated : "") : d.queries_note;
                $(".queries_note").text(note);
                fillTable("#apps_table", d.apps || [], function (a) { return [a.name, a.count]; }, T.noData, 2);
                fillTable("#sites_table", d.sites || [], function (s) {
                    return [s.domain, s.count, s.blocked ? $('<span class="label label-danger">').text(s.blocked) : ""];
                }, T.noData, 3);
                fillTable("#blocks_table", d.blocks || [], function (b) {
                    return [when(b.start), b.end ? when(b.end) : T.ongoing, fmt(b.duration), reasonLabels[b.reason] || b.reason];
                }, T.noData, 4);
                const activity = (d.events || []).map(function (e) { return {ts: e.ts, text: describe(e)}; })
                    .concat((d.queries || []).map(function (q) {
                        return {ts: q.ts, text: format(q.blocked ? T.blockedQuery : T.visited, q.domain)};
                    }));
                activity.sort(function (a, b) { return b.ts - a.ts; });
                fillTable("#activity_table", activity.slice(0, 50), function (a) { return [when(a.ts), a.text]; }, T.noData, 2);
            });
        }

        $("#emergencyAct").click(function () {
            stdDialogConfirm(T.emergency, T.confirmEmergency, T.yes, T.no, function () {
                ajaxCall(svc + "emergency", {}, function () {
                    stdDialogInform(T.emergency, T.stopped, T.close);
                    setTimeout(refresh, 1500);
                });
            });
        });

        function refresh() {
            loadUsage();
            loadRequests();
            if (selected) { loadStats(); }
        }
        refresh();
        setInterval(refresh, 15000);
    });
</script>

<div class="content-box">
    <div class="col-md-12" style="padding-top: 1em;">
        <button id="emergencyAct" type="button" class="btn btn-danger btn-sm pull-right" style="margin-bottom: 0.5em;">
            <span class="fa fa-stop-circle fa-fw"></span> {{ lang._('Emergency stop') }}</button>
        <div id="usage_warning" class="alert alert-warning hidden">
            {{ lang._('The daemon is not running; the status shown is not current. Enable the plugin on the Administration page and click Apply.') }}
        </div>
        <div id="backend_status" class="alert hidden"></div>
        <table id="usage_table" class="table table-striped table-condensed">
            <thead><tr><th>{{ lang._('Group') }}</th><th>{{ lang._('Status') }}</th><th>{{ lang._('Today') }}</th><th>{{ lang._('Budget') }}</th><th>{{ lang._('Remaining') }}</th><th>{{ lang._('Actions') }}</th></tr></thead>
            <tbody></tbody>
        </table>
        <p class="text-muted">{{ lang._('Time used is the time during which at least one device of the group had network traffic. Simultaneous use of several devices counts once. It is an approximation of screen time. Click a group for details.') }}</p>

        <h4 style="margin-top: 1.5em;">{{ lang._('Requests for extra time') }} <span id="req_badge" class="badge hidden"></span></h4>
        <table id="req_table" class="table table-striped table-condensed">
    <thead><tr><th>{{ lang._('When') }}</th><th>{{ lang._('Group') }}</th><th>{{ lang._('Requested') }}</th><th>{{ lang._('Status') }}</th><th>{{ lang._('Decision') }}</th></tr></thead>
    <tbody></tbody>
</table>

    </div>
</div>

<div id="group_detail" class="content-box hidden" style="margin-top: 1em; padding-bottom: 1.5em;">
    <div class="col-md-12">
        <h3 id="detail_title" style="margin-bottom: 0.3em;"></h3>
        <div class="btn-group" id="period_buttons" role="group">
            <button type="button" class="btn btn-default active" data-period="day">{{ lang._('Day') }}</button>
            <button type="button" class="btn btn-default" data-period="week">{{ lang._('Week') }}</button>
            <button type="button" class="btn btn-default" data-period="month">{{ lang._('Month') }}</button>
        </div>
        <span id="detail_total" class="text-muted" style="margin-left: 1em;"></span>

        <h4>{{ lang._('Time used per day') }}</h4>
        <table id="usage_days" class="table table-striped table-condensed">
    <thead><tr><th>{{ lang._('Date') }}</th><th>{{ lang._('Time') }}</th><th>{{ lang._('') }}</th></tr></thead>
    <tbody></tbody>
</table>

        <div class="row">
            <div class="col-md-6">
                <h4>{{ lang._('Most used apps') }}</h4>
                <table id="apps_table" class="table table-striped table-condensed">
    <thead><tr><th>{{ lang._('App') }}</th><th>{{ lang._('Queries') }}</th></tr></thead>
    <tbody></tbody>
</table>

            </div>
            <div class="col-md-6">
                <h4>{{ lang._('Most visited sites') }}</h4>
                <table id="sites_table" class="table table-striped table-condensed">
    <thead><tr><th>{{ lang._('Site') }}</th><th>{{ lang._('Queries') }}</th><th>{{ lang._('Blocked') }}</th></tr></thead>
    <tbody></tbody>
</table>

            </div>
        </div>
        <p class="text-muted queries_note"></p>

        <h4>{{ lang._('Block history') }}</h4>
        <table id="blocks_table" class="table table-striped table-condensed">
    <thead><tr><th>{{ lang._('Start') }}</th><th>{{ lang._('End') }}</th><th>{{ lang._('Duration') }}</th><th>{{ lang._('Reason') }}</th></tr></thead>
    <tbody></tbody>
</table>

        <h4>{{ lang._('Most recent activity') }}</h4>
        <table id="activity_table" class="table table-striped table-condensed">
    <thead><tr><th>{{ lang._('When') }}</th><th>{{ lang._('What') }}</th></tr></thead>
    <tbody></tbody>
</table>

    </div>
</div>
