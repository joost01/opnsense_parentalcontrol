<?php

namespace OPNsense\ParentalControl\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Config;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'parentalcontrol';
    protected static $internalModelClass = 'OPNsense\ParentalControl\ParentalControl';

    private const DAY_LABELS = [
        'mon' => 'ma', 'tue' => 'di', 'wed' => 'wo', 'thu' => 'do',
        'fri' => 'vr', 'sat' => 'za', 'sun' => 'zo'
    ];

    /* ---------- algemeen ---------- */

    public function getAction()
    {
        return ['general' => $this->getModel()->general->getNodes()];
    }

    public function setAction()
    {
        $result = ['result' => 'failed'];
        if ($this->request->isPost() && $this->request->hasPost('general')) {
            $mdl = $this->getModel();
            $mdl->general->setNodes($this->request->getPost('general'));
            $msgs = $mdl->performValidation();
            foreach ($msgs as $msg) {
                if (!isset($result['validations'])) {
                    $result['validations'] = [];
                }
                $result['validations'][$msg->getField()] = $msg->getMessage();
            }
            if (empty($result['validations'])) {
                $mdl->serializeToConfig();
                Config::getInstance()->save();
                $result['result'] = 'saved';
            }
        }
        return $result;
    }

    /* ---------- hulpfuncties ---------- */

    private function groupNames()
    {
        $names = [];
        foreach ($this->getModel()->groups->group->iterateItems() as $uuid => $group) {
            $names[$uuid] = (string)$group->name;
        }
        return $names;
    }

    private function resolveGroups($result)
    {
        $names = $this->groupNames();
        foreach ($result['rows'] as &$row) {
            if (isset($row['group'])) {
                $row['group'] = $names[$row['group']] ?? $row['group'];
            }
        }
        return $result;
    }

    /* ---------- groepen ---------- */

    public function searchGroupAction()
    {
        return $this->searchBase('groups.group', ['enabled', 'name', 'description'], 'name');
    }

    public function getGroupAction($uuid = null)
    {
        return $this->getBase('group', 'groups.group', $uuid);
    }

    public function addGroupAction()
    {
        return $this->addBase('group', 'groups.group');
    }

    public function setGroupAction($uuid)
    {
        return $this->setBase('group', 'groups.group', $uuid);
    }

    public function delGroupAction($uuid)
    {
        $mdl = $this->getModel();
        foreach ($mdl->devices->device->iterateItems() as $device) {
            if ((string)$device->group === $uuid) {
                return ['result' => 'failed', 'message' => 'Groep bevat nog apparaten.'];
            }
        }
        foreach ($mdl->bedtimes->bedtime->iterateItems() as $bedtime) {
            if ((string)$bedtime->group === $uuid) {
                return ['result' => 'failed', 'message' => 'Groep heeft nog bedtijden.'];
            }
        }
        return $this->delBase('groups.group', $uuid);
    }

    public function toggleGroupAction($uuid, $enabled = null)
    {
        return $this->toggleBase('groups.group', $uuid, $enabled);
    }

    /* ---------- apparaten ---------- */

    public function searchDeviceAction()
    {
        return $this->resolveGroups(
            $this->searchBase('devices.device', ['enabled', 'name', 'mac', 'group'], 'name')
        );
    }

    public function getDeviceAction($uuid = null)
    {
        return $this->getBase('device', 'devices.device', $uuid);
    }

    public function addDeviceAction()
    {
        return $this->addBase('device', 'devices.device');
    }

    public function setDeviceAction($uuid)
    {
        return $this->setBase('device', 'devices.device', $uuid);
    }

    public function delDeviceAction($uuid)
    {
        return $this->delBase('devices.device', $uuid);
    }

    public function toggleDeviceAction($uuid, $enabled = null)
    {
        return $this->toggleBase('devices.device', $uuid, $enabled);
    }

    /* ---------- bedtijden ---------- */

    public function searchBedtimeAction()
    {
        $result = $this->resolveGroups(
            $this->searchBase('bedtimes.bedtime', ['enabled', 'group', 'days', 'start', 'end', 'description'])
        );
        foreach ($result['rows'] as &$row) {
            $days = array_filter(explode(',', (string)$row['days']));
            $row['days'] = implode(' ', array_map(function ($d) {
                return self::DAY_LABELS[$d] ?? $d;
            }, $days));
        }
        return $result;
    }

    public function getBedtimeAction($uuid = null)
    {
        return $this->getBase('bedtime', 'bedtimes.bedtime', $uuid);
    }

    public function addBedtimeAction()
    {
        return $this->addBase('bedtime', 'bedtimes.bedtime');
    }

    public function setBedtimeAction($uuid)
    {
        return $this->setBase('bedtime', 'bedtimes.bedtime', $uuid);
    }

    public function delBedtimeAction($uuid)
    {
        return $this->delBase('bedtimes.bedtime', $uuid);
    }

    public function toggleBedtimeAction($uuid, $enabled = null)
    {
        return $this->toggleBase('bedtimes.bedtime', $uuid, $enabled);
    }
}
