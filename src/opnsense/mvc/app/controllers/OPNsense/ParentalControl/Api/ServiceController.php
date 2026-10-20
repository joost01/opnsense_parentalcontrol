<?php

namespace OPNsense\ParentalControl\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
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
     * Verbruikte tijd per groep (vandaag en de afgelopen dagen) en live status.
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
            $rows[] = [
                'uuid' => $uuid,
                'name' => (string)$group->name,
                'enabled' => $enabled,
                'state' => $data['groups'][$uuid]['state'] ?? 'unknown',
                'history' => $history,
            ];
        }
        usort($rows, function ($a, $b) {
            return strcasecmp($a['name'], $b['name']);
        });
        return ['running' => !empty($data['running']), 'dates' => $dates, 'rows' => $rows];
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
     * Instellingen toepassen: firewall herladen (zodat het anchor geregistreerd
     * wordt) en de daemon herstarten of stoppen.
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
        }
        return ['status' => 'ok'];
    }
}
