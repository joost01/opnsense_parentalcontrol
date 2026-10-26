<?php

namespace OPNsense\ParentalControl\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\ParentalControl\ParentalControl;

class ServiceController extends ApiControllerBase
{
    public function statusAction()
    {
        $response = trim((new Backend())->configdRun('parentalcontrol status'));
        $status = strpos($response, 'is running') !== false ? 'running' : 'stopped';
        return ['status' => $status, 'message' => $response];
    }

    /**
     * Time used per group (today and the past days) and live status.
     */
    public function usageAction()
    {
        $mdl = new ParentalControl();
        $data = json_decode(trim((new Backend())->configdRun('parentalcontrol usage')), true);
        if (!is_array($data)) {
            $data = [];
        }
        $dates = [];
        for ($i = 6; $i >= 0; $i--) {
            $dates[] = date('Y-m-d', strtotime("-$i day"));
        }
        $rows = [];
        foreach ($mdl->groups->group->iterateItems() as $uuid => $group) {
            $history = [];
            foreach ($dates as $day) {
                $history[$day] = (int)round($data['history'][$day][$uuid] ?? 0);
            }
            $enabled = (string)$group->enabled === '1';
            $info = $data['groups'][$uuid] ?? [];
            $rows[] = [
                'uuid' => $uuid,
                'name' => (string)$group->name,
                'enabled' => $enabled,
                'state' => $info['state'] ?? 'unknown',
                'limit' => isset($info['limit']) ? (int)round($info['limit']) : null,
                'extra' => (int)round($info['extra'] ?? 0),
                'override' => $info['override'] ?? null,
                'history' => $history,
            ];
        }
        usort($rows, function ($a, $b) {
            return strcasecmp($a['name'], $b['name']);
        });
        return [
            'running' => !empty($data['running']),
            'dates' => $dates,
            'rows' => $rows,
            'backend' => $data['backend'] ?? null,
        ];
    }

    /**
     * Add extra time to the daily time of a group (today).
     * POST: group=<uuid>, minutes=1..720
     */
    public function addTimeAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $uuid = (string)$this->request->getPost('group', 'string', '');
        $minutes = (int)$this->request->getPost('minutes', 'int', 0);
        $known = false;
        foreach ((new ParentalControl())->groups->group->iterateItems() as $id => $group) {
            if ($id === $uuid) {
                $known = true;
            }
        }
        if (!$known || $minutes < 1 || $minutes > 720) {
            return ['status' => 'failed', 'message' => gettext('Invalid input.')];
        }
        $response = trim((new Backend())->configdpRun('parentalcontrol addtime', [$uuid, (string)$minutes]));
        return ['status' => $response === 'OK' ? 'ok' : 'failed', 'message' => $response];
    }

    /**
     * Requests for extra time (open requests first).
     */
    public function requestsAction()
    {
        $data = json_decode(trim((new Backend())->configdRun('parentalcontrol requests')), true);
        return ['rows' => is_array($data) ? $data : []];
    }

    /**
     * Approve or reject a request. POST: id=<hex>, decision=approve|deny
     */
    public function resolveRequestAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $id = (string)$this->request->getPost('id', 'string', '');
        $decision = (string)$this->request->getPost('decision', 'string', '');
        if (!preg_match('/^[0-9a-f]{6,32}$/', $id) || !in_array($decision, ['approve', 'deny'], true)) {
            return ['status' => 'failed', 'message' => gettext('Invalid input.')];
        }
        $response = trim((new Backend())->configdpRun('parentalcontrol resolve', [$id, $decision]));
        return ['status' => $response === 'OK' ? 'ok' : 'failed', 'message' => $response];
    }

    /**
     * Send a test notification with the saved settings.
     */
    public function testNotificationAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $response = trim((new Backend())->configdRun('parentalcontrol testnotify'));
        return ['status' => $response === 'OK' ? 'ok' : 'failed', 'message' => $response];
    }

    /**
     * Test the connection to the filter backend with the saved settings.
     */
    public function testBackendAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $response = trim((new Backend())->configdRun('parentalcontrol backendtest'));
        return ['status' => strpos($response, 'OK') === 0 ? 'ok' : 'failed', 'message' => $response];
    }

    /**
     * Day controls for one group. POST: group=<uuid>, command=block_day|open_day|normal_day|reset_counters
     */
    public function groupCommandAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $uuid = (string)$this->request->getPost('group', 'string', '');
        $command = (string)$this->request->getPost('command', 'string', '');
        $commands = ['block_day', 'open_day', 'normal_day', 'reset_counters'];
        if (!$this->groupExists($uuid) || !in_array($command, $commands, true)) {
            return ['status' => 'failed', 'message' => gettext('Invalid input.')];
        }
        $response = trim((new Backend())->configdpRun('parentalcontrol groupcmd', [$uuid, $command]));
        return ['status' => $response === 'OK' ? 'ok' : 'failed', 'message' => $response];
    }

    /**
     * Statistics for one group. Parameters: group=<uuid>, period=day|week|month
     */
    public function statsAction()
    {
        $uuid = (string)$this->request->get('group', 'string', '');
        $period = (string)$this->request->get('period', 'string', 'day');
        if (!$this->groupExists($uuid) || !in_array($period, ['day', 'week', 'month'], true)) {
            return ['status' => 'failed', 'message' => gettext('Invalid input.')];
        }
        $raw = (new Backend())->configdpRun('parentalcontrol groupstats', [$uuid, $period]);
        $data = json_decode(trim($raw), true);
        if (!is_array($data) || isset($data['error'])) {
            return ['status' => 'failed', 'message' => gettext('No statistics available.')];
        }
        $data['status'] = 'ok';
        return $data;
    }

    private function groupExists($uuid)
    {
        foreach ((new ParentalControl())->groups->group->iterateItems() as $id => $group) {
            if ($id === $uuid) {
                return true;
            }
        }
        return false;
    }

    /**
     * Emergency stop: disable the plugin, release every block and stop the daemon.
     * The settings are kept; enable the plugin again on the Administration page.
     */
    public function emergencyAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $mdl = new ParentalControl();
        $mdl->general->enabled = '0';
        $mdl->serializeToConfig(false, true);
        Config::getInstance()->save();
        $backend = new Backend();
        $backend->configdRun('filter reload');
        $backend->configdRun('parentalcontrol emergency');
        return ['status' => 'ok'];
    }

    /**
     * Remove everything the plugin created in the filter backend (clients, groups, rules).
     */
    public function cleanupBackendAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $response = trim((new Backend())->configdRun('parentalcontrol backendcleanup'));
        return ['status' => strpos($response, 'OK') === 0 ? 'ok' : 'failed', 'message' => $response];
    }

    public function startAction()
    {
        if ($this->request->isPost()) {
            return ['response' => trim((new Backend())->configdRun('parentalcontrol start'))];
        }
        return ['response' => 'failed'];
    }

    public function stopAction()
    {
        if ($this->request->isPost()) {
            return ['response' => trim((new Backend())->configdRun('parentalcontrol stop'))];
        }
        return ['response' => 'failed'];
    }

    /**
     * Apply the settings: reload the firewall (so the anchor is registered)
     * and restart or stop the daemon.
     */
    public function reconfigureAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new Backend();
        $enabled = (string)(new ParentalControl())->general->enabled === '1';
        $backend->configdRun('filter reload');
        if ($enabled) {
            $backend->configdRun('parentalcontrol restart');
        } else {
            $backend->configdRun('parentalcontrol stop');
            $backend->configdRun('parentalcontrol cleanup');
            // a disabled plugin must not leave website profiles behind in the filter backend
            $backend->configdRun('parentalcontrol backendcleanup');
        }
        return ['status' => 'ok'];
    }
}
