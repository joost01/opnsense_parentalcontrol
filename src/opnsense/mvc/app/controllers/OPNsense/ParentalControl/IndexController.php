<?php

namespace OPNsense\ParentalControl;

/**
 * The plugin root only redirects to the overview page.
 */
class IndexController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        return $this->response->redirect('/ui/parentalcontrol/overview');
    }
}
