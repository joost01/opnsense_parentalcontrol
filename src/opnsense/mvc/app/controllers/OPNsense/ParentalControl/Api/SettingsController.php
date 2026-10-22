<?php

namespace OPNsense\ParentalControl\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'parentalcontrol';
    protected static $internalModelClass = 'OPNsense\ParentalControl\ParentalControl';

    private const CATEGORY_LABELS = [
        'social' => 'Sociale media', 'messaging' => 'Chat', 'games' => 'Games',
        'video' => 'Video', 'dating' => 'Dating'
    ];

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
            foreach (['group', 'groups'] as $field) {
                if (isset($row[$field])) {
                    $parts = array_filter(explode(',', (string)$row[$field]));
                    $row[$field] = implode(', ', array_map(function ($p) use ($names) {
                        return $names[$p] ?? $p;
                    }, $parts));
                }
            }
        }
        return $result;
    }

    private function resolveProfiles($result)
    {
        $names = [];
        foreach ($this->getModel()->profiles->profile->iterateItems() as $uuid => $profile) {
            $names[$uuid] = (string)$profile->name;
        }
        foreach ($result['rows'] as &$row) {
            if (isset($row['profile'])) {
                $row['profile'] = $names[$row['profile']] ?? $row['profile'];
            }
        }
        return $result;
    }

    private function usedMacs()
    {
        $used = [];
        foreach ($this->getModel()->devices->device->iterateItems() as $device) {
            $used[strtolower((string)$device->mac)] = true;
        }
        return $used;
    }

    /* ---------- groepen ---------- */

    public function searchGroupAction()
    {
        return $this->resolveProfiles(
            $this->searchBase('groups.group', ['enabled', 'name', 'description', 'profile'], 'name')
        );
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
            if (in_array($uuid, explode(',', (string)$bedtime->groups))) {
                return ['result' => 'failed', 'message' => 'Groep wordt nog gebruikt in een bedtijd.'];
            }
        }
        foreach ($mdl->budgets->budget->iterateItems() as $budget) {
            if (in_array($uuid, explode(',', (string)$budget->groups))) {
                return ['result' => 'failed', 'message' => 'Groep wordt nog gebruikt in een tijdbudget.'];
            }
        }
        return $this->delBase('groups.group', $uuid);
    }

    public function toggleGroupAction($uuid, $enabled = null)
    {
        return $this->toggleBase('groups.group', $uuid, $enabled);
    }

    /* ---------- websiteprofielen ---------- */

    public function searchProfileAction()
    {
        $result = $this->searchBase(
            'profiles.profile',
            ['name', 'description', 'safesearch', 'parental', 'categories', 'blocked_domains'],
            'name'
        );
        foreach ($result['rows'] as &$row) {
            $keys = array_filter(explode(',', (string)$row['categories']));
            $row['categories'] = implode(', ', array_map(function ($k) {
                return self::CATEGORY_LABELS[$k] ?? $k;
            }, $keys));
        }
        return $result;
    }

    public function getProfileAction($uuid = null)
    {
        return $this->getBase('profile', 'profiles.profile', $uuid);
    }

    public function addProfileAction()
    {
        return $this->addBase('profile', 'profiles.profile');
    }

    public function setProfileAction($uuid)
    {
        return $this->setBase('profile', 'profiles.profile', $uuid);
    }

    public function delProfileAction($uuid)
    {
        foreach ($this->getModel()->groups->group->iterateItems() as $group) {
            if ((string)$group->profile === $uuid) {
                return ['result' => 'failed', 'message' => 'Profiel wordt nog gebruikt door een groep.'];
            }
        }
        return $this->delBase('profiles.profile', $uuid);
    }

    /* ---------- apparaten ---------- */

    public function searchDeviceAction()
    {
        return $this->resolveGroups(
            $this->searchBase('devices.device', ['enabled', 'name', 'mac', 'group'], 'name')
        );
    }

    /**
     * Apparaten die OPNsense kent (DHCP-reserveringen, leases, ARP/NDP),
     * zonder de MAC-adressen die al in de plugin staan.
     */
    public function knownDevicesAction()
    {
        $list = json_decode(trim((new Backend())->configdRun('parentalcontrol devices')), true);
        $used = $this->usedMacs();
        $rows = [];
        foreach (is_array($list) ? $list : [] as $item) {
            if (!empty($item['mac']) && !isset($used[$item['mac']])) {
                $rows[] = $item;
            }
        }
        return ['rows' => $rows];
    }

    /**
     * Voeg meerdere bekende apparaten tegelijk toe aan een groep.
     * POST: group=<uuid>, devices[n][mac], devices[n][name]
     */
    public function addKnownDevicesAction()
    {
        $result = ['result' => 'failed'];
        if (!$this->request->isPost()) {
            return $result;
        }
        $group = (string)$this->request->getPost('group', 'string', '');
        $devices = $this->request->getPost('devices');
        if ($group === '' || !is_array($devices) || count($devices) == 0) {
            $result['message'] = 'Kies een groep en minimaal een apparaat.';
            return $result;
        }
        $mdl = $this->getModel();
        $used = $this->usedMacs();
        $added = 0;
        foreach ($devices as $dev) {
            $mac = strtolower(trim((string)($dev['mac'] ?? '')));
            if ($mac === '' || isset($used[$mac])) {
                continue;
            }
            $name = trim(preg_replace('/[^\p{L}0-9 _.\-]/u', '', (string)($dev['name'] ?? '')));
            $name = mb_substr($name, 0, 64);
            if ($name === '') {
                $name = 'Apparaat ' . substr(str_replace(':', '', $mac), -4);
            }
            $node = $mdl->devices->device->Add();
            $node->setNodes(['enabled' => '1', 'name' => $name, 'mac' => $mac, 'group' => $group]);
            $used[$mac] = true;
            $added++;
        }
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
            $result = ['result' => 'saved', 'added' => $added];
        }
        return $result;
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
            $this->searchBase('bedtimes.bedtime', ['enabled', 'groups', 'days', 'start', 'end', 'description'])
        );
        return $this->formatDays($result);
    }

    private function formatDays($result)
    {
        foreach ($result['rows'] as &$row) {
            $days = array_filter(explode(',', (string)$row['days']));
            $row['days'] = implode(' ', array_map(function ($d) {
                return self::DAY_LABELS[$d] ?? $d;
            }, $days));
        }
        return $result;
    }

    /* ---------- tijdbudgetten ---------- */

    public function searchBudgetAction()
    {
        $result = $this->formatDays($this->resolveGroups(
            $this->searchBase('budgets.budget', ['enabled', 'groups', 'days', 'minutes', 'description'])
        ));
        foreach ($result['rows'] as &$row) {
            $min = (int)$row['minutes'];
            $row['minutes'] = intdiv($min, 60) . 'u ' . sprintf('%02d', $min % 60) . 'm';
        }
        return $result;
    }

    public function getBudgetAction($uuid = null)
    {
        return $this->getBase('budget', 'budgets.budget', $uuid);
    }

    public function addBudgetAction()
    {
        return $this->addBase('budget', 'budgets.budget');
    }

    public function setBudgetAction($uuid)
    {
        return $this->setBase('budget', 'budgets.budget', $uuid);
    }

    public function delBudgetAction($uuid)
    {
        return $this->delBase('budgets.budget', $uuid);
    }

    public function toggleBudgetAction($uuid, $enabled = null)
    {
        return $this->toggleBase('budgets.budget', $uuid, $enabled);
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
