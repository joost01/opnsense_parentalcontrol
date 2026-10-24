<?php

/**
 * Plugin hooks for parental control.
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
            'description' => gettext('Parental control'),
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

function parentalcontrol_syslog()
{
    $logfacilities = array();
    $logfacilities['parentalcontrol'] = array('facility' => array('parentalcontrold'));
    return $logfacilities;
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
 * Registers a pf anchor (filter, rdr and nat). The daemon loads the rules and tables into this anchor itself,
 * so the firewall does not have to be reloaded for every block.
 */
function parentalcontrol_firewall($fw)
{
    if (!parentalcontrol_enabled()) {
        return;
    }
    foreach (['rdr', 'nat'] as $type) {
        try {
            // rdr: block page and DNS redirect; nat: return path when the DNS filter is in the same subnet as the clients
            $fw->registerAnchor('parentalcontrol', $type, 200, 'head');
        } catch (\Throwable $e) {
            syslog(LOG_WARNING, 'parentalcontrol: registering the ' . $type . ' anchor failed: ' . $e->getMessage());
        }
    }
    $fw->registerAnchor('parentalcontrol', 'fw', 200, 'head');
}
