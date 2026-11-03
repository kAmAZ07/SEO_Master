<?php
if (!defined('ABSPATH')) { exit; }

function seo_master_upgrade_required() {
    return new WP_Error('upgrade_required', 'Legacy mutations are disabled; a trusted v2 gateway is required.', array('status' => 426));
}
function seo_master_handle_meta_patch($request) { return seo_master_upgrade_required(); }
function seo_master_handle_schema_patch($request) { return seo_master_upgrade_required(); }
function seo_master_handle_interlinks_patch($request) { return seo_master_upgrade_required(); }
function seo_master_handle_patch_request($request, $change_type) { return seo_master_upgrade_required(); }
function seo_master_apply_changes($post_id, $change_type, $operations) { return seo_master_upgrade_required(); }
function seo_master_apply_meta_change($post_id, $op, $path, $value) { return seo_master_upgrade_required(); }
function seo_master_apply_schema_change($post_id, $op, $path, $value) { return seo_master_upgrade_required(); }
function seo_master_apply_interlinks_change($post_id, $op, $path, $value) { return seo_master_upgrade_required(); }

function seo_master_register_routes() {
    register_rest_route('seo-master/v1', '/health', array('methods' => 'GET', 'callback' => 'seo_master_health', 'permission_callback' => '__return_true'));
    foreach (array('meta', 'schema', 'interlinks') as $route) {
        register_rest_route('seo-master/v1', '/' . $route, array('methods' => 'PATCH', 'callback' => 'seo_master_upgrade_required', 'permission_callback' => '__return_true'));
    }
    register_rest_route('seo-master/v2', '/capabilities', array('methods' => 'GET', 'callback' => 'seo_master_capabilities', 'permission_callback' => '__return_true'));
    foreach (array('challenge', 'snapshot', 'dry-run', 'apply') as $route) {
        register_rest_route('seo-master/v2', '/' . $route, array('methods' => 'POST', 'callback' => 'seo_master_v2_request', 'permission_callback' => 'seo_master_v2_permission'));
    }
    register_rest_route('seo-master/v2', '/operations/(?P<operation_id>[0-9a-f-]{36})', array(
        'methods' => 'GET', 'callback' => 'seo_master_v2_request', 'permission_callback' => 'seo_master_v2_permission',
    ));
}
function seo_master_health() {
    return array('status' => 'discovery_only', 'plugin' => 'seo-master-connector', 'version' => SEO_MASTER_CONNECTOR_VERSION, 'protocol' => 'seo-master/v2');
}
function seo_master_v2_config() {
    return new SM_V2_Config(
        (string) get_option('seo_master_project_id', ''), (string) get_option('seo_master_site_id', ''),
        seo_master_get_env_value(array('SEO_MASTER_KEY_ID')), seo_master_get_env_value(array('SEO_MASTER_HMAC_SECRET')),
        seo_master_get_env_value(array('SEO_MASTER_RECEIPT_SECRET')),
        seo_master_get_env_value(array('SEO_MASTER_TRUSTED_GATEWAY')) === '1',
        !seo_master_has_supported_seo_plugin()
    );
}
function seo_master_v2_store() {
    global $wpdb;
    if (!($wpdb instanceof wpdb) || !($wpdb->dbh instanceof mysqli)) { throw new SM_V2_Error('unsupported_storage', 503); }
    return new SM_V2_WPDB_Store(new SM_V2_Mysqli($wpdb, $wpdb->dbh), static function (int $entity_id): void {
        wp_cache_delete($entity_id, 'post_meta');
    });
}
function seo_master_capabilities() {
    global $wp_version;
    $paired = false;
    $storage = false;
    $gateway = false;
    try {
        $config = seo_master_v2_config();
        $paired = true;
        $gateway = $config->can_publish();
        seo_master_v2_store()->check_storage();
        $storage = true;
    } catch (SM_V2_Error $error) {
        // Public discovery exposes only availability, never SQL, secrets or project/site identity.
    }
    return array(
        'protocol' => 'seo-master/v2', 'plugin_version' => SEO_MASTER_CONNECTOR_VERSION, 'wordpress_version' => (string) $wp_version,
        'php_version' => PHP_VERSION, 'paired' => $paired, 'storage_ready' => $storage,
        'apply_enabled' => $paired && $storage && $gateway, 'hitl_authority' => 'trusted_gateway_required',
        'targets' => array('published_unprotected_post', 'published_unprotected_page'),
        'managed_fields' => seo_master_has_supported_seo_plugin() ? array('title', 'description') : array('title', 'description', 'jsonld'),
        'operations' => array('set', 'remove'),
        'unsupported_manual' => array('post_title', 'h1', 'content', 'html', 'internal_links', 'rollback_endpoint'),
        'storage_model' => 'innodb_atomic_meta_and_ledger_v2', 'snapshot_digest' => 'canonical_json_sha256_v2',
        'runtime_validation' => 'pending_real_wordpress_innodb_matrix',
        'yoast' => 'unverified_title_description_filters_jsonld_manual', 'rank_math' => 'unverified_title_description_filters_jsonld_manual',
        'limitations' => array('theme_head_duplicates_require_verification', 'no_global_hook_exactly_once', 'release_package_not_verified'),
        'limits' => array('body_bytes' => 16384, 'changes' => 3, 'title_bytes' => 512, 'description_bytes' => 2000, 'jsonld_bytes' => 8192, 'clock_skew_seconds' => 300),
    );
}

function seo_master_v2_permission($request) {
    // HMAC authorization is performed once in the callback against the untouched bytes, not cookies or a user id in JSON.
    if (strlen($request->get_body()) > 16384) { return new WP_Error('request_too_large', 'Request too large.', array('status' => 413)); }
    $query = $request->get_query_params();
    unset($query['rest_route']);
    if ($query !== array() || ($request->get_method() === 'POST' && strtolower(trim(explode(';', $request->get_header('content-type'))[0])) !== 'application/json')) {
        return new WP_Error('invalid_request', 'Only the exact JSON contract is accepted.', array('status' => 400));
    }
    return true;
}
function seo_master_v2_request($request) {
    try {
        $headers = array();
        foreach (array('project-id', 'site-id', 'key-id', 'timestamp', 'nonce', 'operation-id', 'signature') as $name) {
            $headers['x-seo-' . $name] = $request->get_header('x-seo-' . $name);
        }
        $service = new SM_V2_Service(seo_master_v2_config(), seo_master_v2_store(), SEO_MASTER_CONNECTOR_VERSION);
        $result = $service->handle($request->get_method(), $request->get_route(), $request->get_body(), $headers, time());
        if (($result['kind'] ?? null) === 'receipt' && isset($result['entity_id']) && is_int($result['entity_id'])) {
            // Also heals post-meta cache after a committed write whose response/cache invalidation was interrupted.
            wp_cache_delete($result['entity_id'], 'post_meta');
        }
        $response = new WP_REST_Response($result, 200);
        $response->header('Cache-Control', 'no-store, private');
        return $response;
    } catch (SM_V2_Error $error) {
        return new WP_Error($error->error_code, 'SEO Master request rejected: ' . $error->error_code, array('status' => $error->http_status));
    } catch (Throwable $error) {
        return new WP_Error('internal_failure', 'SEO Master request failed closed; reconcile the same operation id.', array('status' => 503));
    }
}
