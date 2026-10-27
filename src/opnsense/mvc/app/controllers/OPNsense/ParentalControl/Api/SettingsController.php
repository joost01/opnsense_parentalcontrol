<?php

namespace OPNsense\ParentalControl\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'parentalcontrol';
    protected static $internalModelClass = 'OPNsense\ParentalControl\ParentalControl';

    private function categoryLabel($key)
    {
        $labels = [
            'social' => gettext('Social media'), 'messaging' => gettext('Chat'), 'games' => gettext('Games'),
            'video' => gettext('Video'), 'dating' => gettext('Dating')
        ];
        return $labels[$key] ?? $key;
    }

    private function dayLabel($key)
    {
        $labels = [
            'mon' => gettext('Mon'), 'tue' => gettext('Tue'), 'wed' => gettext('Wed'), 'thu' => gettext('Thu'),
            'fri' => gettext('Fri'), 'sat' => gettext('Sat'), 'sun' => gettext('Sun')
        ];
        return $labels[$key] ?? $key;
    }

    /* ---------- general ---------- */

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
            $foreign = ['groups.', 'profiles.', 'devices.', 'bedtimes.', 'budgets.'];
            foreach ($mdl->performValidation() as $msg) {
                $field = $msg->getField();
                $isForeign = false;
                foreach ($foreign as $prefix) {
                    if (strpos($field, $prefix) === 0) {
                        $isForeign = true;
                    }
                }
                if ($isForeign) {
                    // an error in another part of the configuration blocks saving: show it on the first
                    // field of this form instead of failing silently
                    $result['validations']['general.enabled'] = sprintf(
                        gettext('Another part of the configuration is invalid (%s): %s'),
                        $field,
                        $msg->getMessage()
                    );
                } else {
                    $key = strpos($field, 'general.') === 0 ? $field : 'general.' . $field;
                    $result['validations'][$key] = $msg->getMessage();
                }
            }
            if (empty($result['validations'])) {
                $mdl->serializeToConfig();
                Config::getInstance()->save();
                $result['result'] = 'saved';
            }
        }
        return $result;
    }

    /* ---------- helpers ---------- */

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

    /* ---------- groups ---------- */

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
                return ['result' => 'failed', 'message' => gettext('The group still contains devices.')];
            }
        }
        foreach ($mdl->bedtimes->bedtime->iterateItems() as $bedtime) {
            if (in_array($uuid, explode(',', (string)$bedtime->groups))) {
                return ['result' => 'failed', 'message' => gettext('The group is still used in a bedtime.')];
            }
        }
        foreach ($mdl->budgets->budget->iterateItems() as $budget) {
            if (in_array($uuid, explode(',', (string)$budget->groups))) {
                return ['result' => 'failed', 'message' => gettext('The group is still used in a time budget.')];
            }
        }
        return $this->delBase('groups.group', $uuid);
    }

    public function toggleGroupAction($uuid, $enabled = null)
    {
        return $this->toggleBase('groups.group', $uuid, $enabled);
    }

    /* ---------- website profiles ---------- */

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
                return $this->categoryLabel($k);
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
                return ['result' => 'failed', 'message' => gettext('The profile is still used by a group.')];
            }
        }
        return $this->delBase('profiles.profile', $uuid);
    }

    /* ---------- devices ---------- */

    public function searchDeviceAction()
    {
        return $this->resolveGroups(
            $this->searchBase('devices.device', ['enabled', 'name', 'mac', 'group'], 'name')
        );
    }

    /**
     * Devices OPNsense knows about (DHCP reservations, leases, ARP/NDP),
     * without the MAC addresses that are already in the plugin.
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
     * Add several known devices to a group at once.
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
            $result['message'] = gettext('Choose a group and at least one device.');
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
                $name = gettext('Device') . ' ' . substr(str_replace(':', '', $mac), -4);
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

    /* ---------- bedtimes ---------- */

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
                return $this->dayLabel($d);
            }, $days));
        }
        return $result;
    }

    /* ---------- time budgets ---------- */

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
