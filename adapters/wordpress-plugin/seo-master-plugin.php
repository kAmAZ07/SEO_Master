<?php
/**
 * Plugin Name: SEO Master Connector
 * Description: Secure connector that applies SEO updates received from SEO Master platform.
 * Version: 0.2.0
 * Author: SEO Master Team
 * Requires at least: 6.7
 * Requires PHP: 8.0
 * Text Domain: seo-master-connector
 */

if (!defined('ABSPATH')) {
    exit;
}

define('SEO_MASTER_CONNECTOR_VERSION', '0.2.0');
define('SEO_MASTER_CONNECTOR_PATH', plugin_dir_path(__FILE__));
define('SEO_MASTER_CONNECTOR_URL', plugin_dir_url(__FILE__));

require_once SEO_MASTER_CONNECTOR_PATH . 'includes/compatibility-checker.php';

if (!seo_master_is_compatible()) {
    add_action('admin_notices', 'seo_master_render_incompatibility_notice');
    return;
}

require_once SEO_MASTER_CONNECTOR_PATH . 'includes/signature-verifier.php';
require_once SEO_MASTER_CONNECTOR_PATH . 'includes/hmac-validator.php';
require_once SEO_MASTER_CONNECTOR_PATH . 'includes/api-handler.php';
require_once SEO_MASTER_CONNECTOR_PATH . 'includes/meta-filter.php';
require_once SEO_MASTER_CONNECTOR_PATH . 'admin/settings.php';

/**
 * Initialize default options.
 */
function seo_master_activate_plugin() {
    if (!seo_master_verify_current_plugin_signature()) {
        wp_die(
            esc_html__('SEO Master Connector signature verification failed. Check SEO_MASTER_PLUGIN_SIGNATURE before activation.', 'seo-master-connector')
        );
    }

    if (get_option('seo_master_max_drift') === false) {
        add_option('seo_master_max_drift', 300);
    }

    if (get_option('seo_master_project_id') === false) {
        add_option('seo_master_project_id', '');
    }

    delete_option('seo_master_hmac_secret');
}
register_activation_hook(__FILE__, 'seo_master_activate_plugin');

/**
 * Register runtime hooks.
 */
function seo_master_bootstrap() {
    add_action('rest_api_init', 'seo_master_register_routes');
    add_action('admin_menu', 'seo_master_register_settings_page');
    add_action('admin_init', 'seo_master_register_settings');
    add_action('wp_head', 'seo_master_output_meta_tags', 5);
    add_filter('pre_get_document_title', 'seo_master_filter_document_title', 99);
    add_filter('document_title_parts', 'seo_master_filter_document_title_parts', 99);
    add_filter('the_title', 'seo_master_filter_h1_title', 99, 2);
    add_filter('wpseo_title', 'seo_master_filter_seo_title', 99);
    add_filter('wpseo_metadesc', 'seo_master_filter_seo_description', 99);
    add_filter('wpseo_schema_graph', 'seo_master_filter_yoast_schema_graph', 99);
    add_filter('rank_math/frontend/title', 'seo_master_filter_seo_title', 99);
    add_filter('rank_math/frontend/description', 'seo_master_filter_seo_description', 99);
    add_filter('rank_math/json_ld', 'seo_master_filter_rank_math_schema', 99);
    add_filter('upgrader_source_selection', 'seo_master_verify_plugin_update_signature', 10, 4);
}
add_action('plugins_loaded', 'seo_master_bootstrap');
