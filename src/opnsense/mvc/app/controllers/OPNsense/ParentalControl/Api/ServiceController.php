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
