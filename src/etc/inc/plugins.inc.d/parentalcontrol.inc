<?php

/**
 * Plugin-hooks voor ouderlijk toezicht.
 */

function parentalcontrol_enabled()
{
    $mdl = new \OPNsense\ParentalControl\ParentalControl();
    return (string)$mdl->general->enabled === '1';
}

function parentalcontrol_services()
{
    $services = array();
    if (parentalcontrol_enabled()) {
        $services[] = array(
            'description' => gettext('Ouderlijk toezicht'),
            'configd' => array(
                'restart' => array('parentalcontrol restart'),
                'start' => array('parentalcontrol start'),
                'stop' => array('parentalcontrol stop'),
            ),
            'name' => 'parentalcontrold',
            'pidfile' => '/var/run/parentalcontrold.pid'
        );
    }
    return $services;
}

function parentalcontrol_configure()
{
    return array(
        'bootup' => array('parentalcontrol_start'),
    );
}

function parentalcontrol_start()
{
    if (parentalcontrol_enabled()) {
        (new \OPNsense\Core\Backend())->configdRun('parentalcontrol start');
    }
}

/**
 * Registreert een pf-anchor (filter, rdr en nat). De daemon laadt zelf de regels en tables in dit anchor,
 * zodat de firewall niet bij elke blokkade herladen hoeft te worden.
 */
function parentalcontrol_firewall($fw)
{
    if (!parentalcontrol_enabled()) {
        return;
    }
    foreach (['rdr', 'nat'] as $type) {
        try {
            // rdr: blokkeerpagina en DNS-omleiding; nat: terugkeerpad als AdGuard in hetzelfde subnet staat
            $fw->registerAnchor('parentalcontrol', $type, 200, 'head');
        } catch (\Throwable $e) {
            syslog(LOG_WARNING, 'parentalcontrol: ' . $type . '-anchor registreren mislukt: ' . $e->getMessage());
        }
    }
    $fw->registerAnchor('parentalcontrol', 'fw', 200, 'head');
}
