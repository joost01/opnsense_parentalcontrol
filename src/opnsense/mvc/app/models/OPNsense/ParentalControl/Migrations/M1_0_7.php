<?php

namespace OPNsense\ParentalControl\Migrations;

use OPNsense\Base\BaseModelMigration;
use OPNsense\Core\Config;

/**
 * Versie 1.0.7: de AdGuard-instellingen zijn algemene backend-instellingen geworden
 * (adguard_url -> backend_url, enzovoort), zodat ook andere backends (Pi-hole) ze kunnen gebruiken.
 */
class M1_0_7 extends BaseModelMigration
{
    public function run($model)
    {
        $cfg = Config::getInstance()->object();
        if (!isset($cfg->OPNsense->ParentalControl->general)) {
            return;
        }
        $general = $cfg->OPNsense->ParentalControl->general;
        foreach (['url', 'user', 'password', 'verify_tls'] as $field) {
            $old = 'adguard_' . $field;
            if (isset($general->$old) && (string)$general->$old !== '') {
                $model->general->{'backend_' . $field} = (string)$general->$old;
            }
        }
    }
}
