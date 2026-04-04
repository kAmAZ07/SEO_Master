<?php
if (!defined('ABSPATH')) {
    exit;
}

/**
 * Register REST routes used by SEO Master platform.
 */
function seo_master_register_routes() {
    register_rest_route('seo-master/v1', '/health', array(
        'methods' => 'GET',
        'callback' => 'seo_master_health',
        'permission_callback' => '__return_true',
    ));

    register_rest_route('seo-master/v1', '/meta', array(
        'methods' => 'PATCH',
        'callback' => 'seo_master_handle_meta_patch',
        'permission_callback' => '__return_true',
    ));

    register_rest_route('seo-master/v1', '/schema', array(
        'methods' => 'PATCH',
        'callback' => 'seo_master_handle_schema_patch',
        'permission_callback' => '__return_true',
    ));

    register_rest_route('seo-master/v1', '/interlinks', array(
        'methods' => 'PATCH',
        'callback' => 'seo_master_handle_interlinks_patch',
        'permission_callback' => '__return_true',
    ));
}

function seo_master_health() {
    return array(
        'status' => 'ok',
        'plugin' => 'seo-master-connector',
        'version' => SEO_MASTER_CONNECTOR_VERSION,
    );
}

function seo_master_handle_meta_patch($request) {
    return seo_master_handle_patch_request($request, 'meta');
}

function seo_master_handle_schema_patch($request) {
    return seo_master_handle_patch_request($request, 'schema');
}

function seo_master_handle_interlinks_patch($request) {
    return seo_master_handle_patch_request($request, 'interlinks');
}

/**
 * Handle incoming signed patch payload.
 */
function seo_master_handle_patch_request($request, $change_type) {
    $body = $request->get_body();
    $path = wp_parse_url((string) $_SERVER['REQUEST_URI'], PHP_URL_PATH);
    $auth = seo_master_validate_hmac($body, 'PATCH', $path);

    if (is_wp_error($auth)) {
        return $auth;
    }

    $payload = json_decode($body, true);
    if (!is_array($payload)) {
        return new WP_Error('seo_master_payload', 'Invalid JSON payload', array('status' => 400));
    }

    $project_id = isset($payload['project_id']) ? sanitize_text_field((string) $payload['project_id']) : '';
    $entity_id = isset($payload['entity_id']) ? (string) $payload['entity_id'] : '';
    $changes = isset($payload['changes']) && is_array($payload['changes']) ? $payload['changes'] : array();

    if ($project_id === '' || $entity_id === '' || empty($changes)) {
        return new WP_Error('seo_master_payload', 'project_id, entity_id, and changes are required', array('status' => 400));
    }

    $post_id = seo_master_resolve_post_id($entity_id);
    if ($post_id <= 0) {
        return new WP_Error('seo_master_entity', 'Unable to resolve target post from entity_id', array('status' => 404));
    }

    $result = seo_master_apply_changes($post_id, $change_type, $changes);
    if (is_wp_error($result)) {
        return $result;
    }

    return array(
        'status' => 'applied',
        'project_id' => $project_id,
        'post_id' => $post_id,
        'change_type' => $change_type,
        'applied_operations' => (int) $result['applied'],
        'skipped_operations' => (int) $result['skipped'],
    );
}

/**
 * Resolve a WordPress post by numeric ID or URL.
 */
function seo_master_resolve_post_id($entity_id) {
    if (is_numeric($entity_id)) {
        return (int) $entity_id;
    }

    $post_id = url_to_postid($entity_id);
    if ($post_id > 0) {
        return (int) $post_id;
    }

    return 0;
}

/**
 * Apply JSON patch operations to post content/meta.
 */
function seo_master_apply_changes($post_id, $change_type, $operations) {
    $applied = 0;
    $skipped = 0;

    foreach ($operations as $operation) {
        if (!is_array($operation)) {
            $skipped++;
            continue;
        }

        $op = isset($operation['op']) ? strtolower((string) $operation['op']) : '';
        $path = isset($operation['path']) ? strtolower((string) $operation['path']) : '';
        $value = isset($operation['value']) ? $operation['value'] : null;

        if (!in_array($op, array('add', 'replace', 'remove'), true)) {
            $skipped++;
            continue;
        }

        if ($change_type === 'meta') {
            $updated = seo_master_apply_meta_change($post_id, $op, $path, $value);
        } elseif ($change_type === 'schema') {
            $updated = seo_master_apply_schema_change($post_id, $op, $path, $value);
        } elseif ($change_type === 'interlinks') {
            $updated = seo_master_apply_interlinks_change($post_id, $op, $path, $value);
        } else {
            return new WP_Error('seo_master_change_type', 'Unsupported change type', array('status' => 400));
        }

        if ($updated) {
            $applied++;
        } else {
            $skipped++;
        }
    }

    return array('applied' => $applied, 'skipped' => $skipped);
}

function seo_master_apply_meta_change($post_id, $op, $path, $value) {
    $is_remove = ($op === 'remove');

    if ($path === '/title' || $path === '/meta_title') {
        $new_title = $is_remove ? '' : sanitize_text_field((string) $value);
        wp_update_post(array('ID' => $post_id, 'post_title' => $new_title));
        return true;
    }

    if ($path === '/description' || $path === '/meta_description') {
        if ($is_remove) {
            delete_post_meta($post_id, '_seo_master_meta_description');
        } else {
            update_post_meta($post_id, '_seo_master_meta_description', wp_kses_post((string) $value));
        }
        return true;
    }

    if ($path === '/h1') {
        if ($is_remove) {
            delete_post_meta($post_id, '_seo_master_h1');
        } else {
            update_post_meta($post_id, '_seo_master_h1', sanitize_text_field((string) $value));
        }
        return true;
    }

    return false;
}

function seo_master_apply_schema_change($post_id, $op, $path, $value) {
    if ($path !== '/schema' && $path !== '/schema_org' && $path !== '/jsonld') {
        return false;
    }

    if ($op === 'remove') {
        delete_post_meta($post_id, '_seo_master_schema_jsonld');
        return true;
    }

    $schema = is_string($value) ? $value : wp_json_encode($value);
    if (empty($schema)) {
        return false;
    }

    update_post_meta($post_id, '_seo_master_schema_jsonld', $schema);
    return true;
}

function seo_master_apply_interlinks_change($post_id, $op, $path, $value) {
    if (strpos($path, '/internal_links') === false) {
        return false;
    }

    if ($op === 'remove') {
        delete_post_meta($post_id, '_seo_master_interlinks');
        return true;
    }

    $links = array();
    if (is_array($value)) {
        $links = $value;
    } elseif (is_object($value)) {
        $links = array((array) $value);
    }

    update_post_meta($post_id, '_seo_master_interlinks', wp_json_encode($links));
    return true;
}
