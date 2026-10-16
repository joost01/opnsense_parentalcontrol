<?php

namespace OPNsense\ParentalControl;

class IndexController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->generalForm = $this->getForm('general');
        $this->view->formDialogGroup = $this->getForm('dialogGroup');
        $this->view->formDialogDevice = $this->getForm('dialogDevice');
        $this->view->formDialogBedtime = $this->getForm('dialogBedtime');
        $this->view->pick('OPNsense/ParentalControl/index');
    }
}
