<?php
declare(strict_types=1);

// Standalone boundary stubs only. No real WP runtime, mysqli connection, activation or DDL.
define('ABSPATH', __DIR__);
$wp_version = '6.7';
final class AdapterFixture {
    public static array $routes = [];
    public static array $hooks = [];
    public static array $settings = [];
    public static array $options = [];
    public static bool $is_admin = false;
    public static int $settings_nonces = 0;
    public static array $cache_invalidations = [];
}
final class WP_REST_Response {
    public array $headers = [];
    public function __construct(public array $body, public int $status) {}
    public function header(string $name, string $value): void { $this->headers[$name] = $value; }
}
function wp_cache_delete($key, $group): void { AdapterFixture::$cache_invalidations[] = [$key, $group]; }
final class WP_Error {
    public function __construct(public string $code, public string $message = '', public array $details = []) {}
}
final class RequestStub {
    public function __construct(private string $body = '{}', private string $content_type = 'application/json', private array $query = []) {}
    public function get_body(): string { return $this->body; }
    public function get_method(): string { return 'POST'; }
    public function get_header(string $name): string { return $name === 'content-type' ? $this->content_type : ''; }
    public function get_query_params(): array { return $this->query; }
}
function plugin_dir_path($file): string { return dirname($file) . '/'; }
function plugin_dir_url($file): string { return 'https://example.invalid/plugins/seo-master/'; }
function add_action($hook, $callback, ...$arguments): void { AdapterFixture::$hooks[$hook][] = $callback; }
function add_filter($hook, $callback, ...$arguments): void { AdapterFixture::$hooks[$hook][] = $callback; }
function register_activation_hook($file, $callback): void {}
function register_rest_route($namespace, $route, $definition): void { AdapterFixture::$routes[$namespace . $route] = $definition; }
function register_setting($group, $option, $definition): void { AdapterFixture::$settings[$option] = $definition; }
function get_option($option, $default = false) { return AdapterFixture::$options[$option] ?? $default; }
function current_user_can($capability): bool { return AdapterFixture::$is_admin; }
function settings_fields($group): void { ++AdapterFixture::$settings_nonces; }
function submit_button(): void {}
function esc_html($value): string { return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8'); }
function esc_attr($value): string { return esc_html($value); }
function home_url($route): string { return 'https://example.invalid' . $route; }

$failures = [];
$assertions = 0;
function assertion(bool $condition, string $message): void {
    global $failures, $assertions;
    ++$assertions;
    if (!$condition) { $failures[] = $message; }
}
ob_start();
require __DIR__ . '/../../adapters/wordpress-plugin/seo-master-plugin.php';
$bootstrap_output = ob_get_clean();
assertion($bootstrap_output === '', 'bootstrap emitted output/BOM');
seo_master_bootstrap();
seo_master_register_routes();
seo_master_register_settings();
assertion(count(AdapterFixture::$routes) === 14, 'route collection incomplete');
foreach (['meta', 'schema', 'interlinks'] as $route) {
    $definition = AdapterFixture::$routes['seo-master/v1/' . $route];
    $result = $definition['callback']();
    assertion($result instanceof WP_Error && $result->code === 'upgrade_required' && $result->details['status'] === 426, 'v1 mutation reopened');
}
foreach (['challenge', 'snapshot', 'dry-run', 'apply', 'rollback', 'rollback/dry-run', 'target-bound/apply', 'target-bound/dry-run', 'operations/(?P<operation_id>[0-9a-f-]{36})'] as $route) {
    assertion(AdapterFixture::$routes['seo-master/v2/' . $route]['permission_callback'] === 'seo_master_v2_permission', 'sensitive route missing boundary permission');
}
$capabilities = seo_master_capabilities();
assertion(!$capabilities['paired'] && !$capabilities['apply_enabled'] && !$capabilities['storage_ready'], 'incomplete pairing permissive');
assertion(!isset($capabilities['project_id'], $capabilities['site_id']), 'public discovery exposes binding');
assertion(in_array('h1', $capabilities['unsupported_manual'], true), 'H1 incorrectly advertised');
assertion($capabilities['runtime_validation'] === 'pending_real_wordpress_innodb_matrix', 'stub evidence advertised as compatibility');
assertion(($capabilities['rollback_enabled'] ?? null) === false && ($capabilities['rollback_model'] ?? null) === 'per_original_single_use_guard_v2', 'rollback capability untruthful');
assertion(($capabilities['manual_target_aba_detection'] ?? null) === false, 'general target ABA detection incorrectly claimed');
assertion(!in_array('rollback_endpoint', $capabilities['unsupported_manual'], true), 'implemented source endpoint still marked missing');
assertion(!isset(AdapterFixture::$hooks['the_title']), 'unsafe H1 hook still registered');
assertion(!isset(AdapterFixture::$hooks['wpseo_schema_graph'], AdapterFixture::$hooks['rank_math/json_ld']), 'third-party graph hook registered');
assertion(seo_master_v2_permission(new RequestStub()) === true, 'valid boundary rejected');
foreach ([
    new RequestStub(str_repeat('x', 16385)), new RequestStub('{}', 'text/plain'),
    new RequestStub('{}', 'application/json', ['unexpected' => '1']),
] as $request) {
    assertion(seo_master_v2_permission($request) instanceof WP_Error, 'bad request boundary accepted');
}
assertion(seo_master_sanitize_pairing_id('wrong') === '' && seo_master_sanitize_pairing_id(null) === '', 'invalid binding retained');
assertion(AdapterFixture::$settings['seo_master_site_id']['show_in_rest'] === false, 'binding exposed through settings REST');
ob_start();
seo_master_render_settings_page();
assertion(ob_get_clean() === '' && AdapterFixture::$settings_nonces === 0, 'non-admin rendered pairing controls');
AdapterFixture::$is_admin = true;
ob_start();
seo_master_render_settings_page();
$admin_html = ob_get_clean();
assertion(AdapterFixture::$settings_nonces === 1 && str_contains($admin_html, 'seo_master_site_id'), 'admin form lacks Settings API nonce/site binding');
assertion(seo_master_filter_h1_title('Original', 7) === 'Original', 'H1 output changed');
assertion(seo_master_get_supported_wordpress_versions() === [], 'unverified Tested versions advertised');
$rollback_receipt = ['kind' => 'receipt', 'operation_type' => 'rollback', 'entity_id' => 7];
$response = seo_master_v2_response($rollback_receipt);
assertion($response->body === $rollback_receipt && $response->status === 200 &&
    $response->headers['Cache-Control'] === 'no-store, private', 'rollback response changed or cacheable');
assertion(AdapterFixture::$cache_invalidations === [], 'response serializer duplicated service/store cache effects');
foreach ([
    ['kind' => 'receipt', 'entity_id' => 7, 'outcome' => 'no_op', 'applied_fields' => []],
    ['kind' => 'receipt', 'entity_id' => 8, 'signature' => 'unverified raw response array'],
    ['kind' => 'rollback_guard', 'entity_id' => 9],
] as $unverified) {
    $response = seo_master_v2_response($unverified);
    assertion($response->body === $unverified && $response->headers['Cache-Control'] === 'no-store, private' &&
        AdapterFixture::$cache_invalidations === [], 'serializer caused cache effects for no-op/unverified/cross-kind result');
}
foreach ($failures as $failure) { fwrite(STDERR, "FAIL: $failure\n"); }
printf("WP adapter boundaries: %d/%d passed (standalone stubs, not WP runtime)\n", $assertions - count($failures), $assertions);
exit($failures === [] ? 0 : 1);
