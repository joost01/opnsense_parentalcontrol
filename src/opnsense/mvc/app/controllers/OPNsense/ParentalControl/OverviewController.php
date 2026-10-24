<?php

namespace OPNsense\ParentalControl;

/**
 * Overview page: status per group, day controls, requests and group details.
 */
class OverviewController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->pick('OPNsense/ParentalControl/overview');
    }
}
