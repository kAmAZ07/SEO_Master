<?php
if (!defined('ABSPATH')) { exit; }

function seo_master_get_env_value($names) {
    foreach ((array) $names as $name) {
        if (defined($name)) { return (string) constant($name); }
        $value = getenv($name);
        if ($value !== false) { return (string) $value; }
    }
    return '';
}
function seo_master_get_hmac_secret() {
    return seo_master_get_env_value(array('SEO_MASTER_HMAC_SECRET'));
}
function seo_master_validate_hmac($request_body, $method, $path) {
    return new WP_Error('upgrade_required', 'HMAC v1 is disabled.', array('status' => 426));
}
