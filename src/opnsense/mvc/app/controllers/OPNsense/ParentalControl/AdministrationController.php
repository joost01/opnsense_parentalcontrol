<?php

namespace OPNsense\ParentalControl;

/**
 * Administration page: general settings, groups, devices, bedtimes, time budgets and website profiles.
 */
class AdministrationController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->generalForm = $this->getForm('general');
        $this->view->formDialogGroup = $this->getForm('dialogGroup');
        $this->view->formDialogDevice = $this->getForm('dialogDevice');
        $this->view->formDialogBedtime = $this->getForm('dialogBedtime');
        $this->view->formDialogProfile = $this->getForm('dialogProfile');
        $this->view->formDialogBudget = $this->getForm('dialogBudget');
        $this->view->pick('OPNsense/ParentalControl/administration');
    }
}
