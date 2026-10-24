<?php

namespace OPNsense\ParentalControl\Migrations;

use OPNsense\Base\BaseModelMigration;
use OPNsense\Core\Config;

/**
 * Version 1.0.1: a bedtime can apply to several groups.
 * The old field 'group' is copied into 'groups'.
 */
class M1_0_1 extends BaseModelMigration
{
    public function run($model)
    {
        $old = [];
        $cfg = Config::getInstance()->object();
        if (isset($cfg->OPNsense->ParentalControl->bedtimes->bedtime)) {
            foreach ($cfg->OPNsense->ParentalControl->bedtimes->bedtime as $bedtime) {
                $uuid = (string)$bedtime->attributes()['uuid'];
                if (isset($bedtime->group) && (string)$bedtime->group !== '') {
                    $old[$uuid] = (string)$bedtime->group;
                }
            }
        }
        foreach ($model->bedtimes->bedtime->iterateItems() as $uuid => $node) {
            if (isset($old[$uuid])) {
                $node->groups = $old[$uuid];
            }
        }
    }
}
