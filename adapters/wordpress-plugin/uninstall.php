<?php
if (!defined('WP_UNINSTALL_PLUGIN')) {
    exit;
}

delete_option('seo_master_project_id');
delete_option('seo_master_max_drift');
