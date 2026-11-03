<?php
declare(strict_types=1);

// Standalone WP boundary stubs: this is NOT a WordPress/database integration test.
define('ABSPATH', __DIR__);
final class WP_Error {
    public function __construct(public string $code, public string $message = '', public array $details = []) {}
}
final class LegacyFixture {
    public static array $writes = [];
    public static array $meta = [];
}
function is_wp_error($value): bool { return $value instanceof WP_Error; }
function is_singular(): bool { return true; }
function get_queried_object_id(): int { return 7; }
function get_post_meta($post_id, $key, $single) { return LegacyFixture::$meta[$key] ?? ''; }
function update_post_meta(...$arguments): bool { LegacyFixture::$writes[] = $arguments; return true; }
function delete_post_meta(...$arguments): bool { LegacyFixture::$writes[] = $arguments; return true; }
function wp_update_post(...$arguments): int { LegacyFixture::$writes[] = $arguments; return 7; }
function sanitize_text_field($value): string { return strip_tags((string) $value); }
function wp_kses_post($value): string { return (string) $value; }
function wp_json_encode($value, $flags = 0): string { return json_encode($value, $flags | JSON_THROW_ON_ERROR); }
function esc_attr($value): string { return htmlspecialchars($value, ENT_QUOTES, 'UTF-8'); }

require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-protocol.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/api-handler.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/meta-filter.php';

$failures = [];
$tests = [
    'unknown legacy operation rejects without writes' => function (): bool {
        LegacyFixture::$writes = [];
        $result = seo_master_apply_changes(7, 'meta', [['op' => 'invented', 'path' => '/title', 'value' => 'x']]);
        return is_wp_error($result) && LegacyFixture::$writes === [];
    },
    'legacy title entrypoint cannot mutate post_title' => function (): bool {
        LegacyFixture::$writes = [];
        $result = seo_master_apply_meta_change(7, 'replace', '/title', 'Changed');
        return is_wp_error($result) && LegacyFixture::$writes === [];
    },
    'Yoast graph keeps third-party nodes' => function (): bool {
        LegacyFixture::$meta = ['_seo_master_schema_jsonld' => '{"@context":"https://schema.org","@type":"WebPage","name":"Managed"}'];
        $graph = [['@type' => 'BreadcrumbList', '@id' => 'third-party']];
        $result = seo_master_filter_yoast_schema_graph($graph);
        return in_array($graph[0], $result, true);
    },
    'RankMath graph keeps third-party nodes' => function (): bool {
        $graph = ['publisher' => ['@type' => 'Organization', 'name' => 'Third party']];
        $result = seo_master_filter_rank_math_schema($graph);
        return isset($result['publisher']) && $result['publisher'] === $graph['publisher'];
    },
    'JSONLD never closes script element' => function (): bool {
        LegacyFixture::$meta = ['_seo_master_schema_jsonld' => '{"@type":"WebPage","name":"</script><script>alert(1)</script>"}'];
        ob_start();
        seo_master_output_meta_tags();
        $html = ob_get_clean();
        return substr_count(strtolower($html), '</script>') <= 1 && !str_contains($html, '<script>alert');
    },
    'valid basic JSONLD is actually rendered and decodable' => function (): bool {
        LegacyFixture::$meta = ['_seo_master_schema_jsonld' => '{"@context":"https://schema.org","@type":"WebPage","name":"Текст \\"quote\\" 😀 &"}'];
        ob_start();
        seo_master_output_meta_tags();
        $html = ob_get_clean();
        if (preg_match('~<script type="application/ld\\+json">(.*?)</script>~s', $html, $match) !== 1) { return false; }
        return json_decode($match[1], true, 16, JSON_THROW_ON_ERROR) === json_decode(LegacyFixture::$meta['_seo_master_schema_jsonld'], true, 16, JSON_THROW_ON_ERROR) &&
            substr_count($html, '<script') === 1;
    },
    'managed description escapes attributes but title keeps exact bytes' => function (): bool {
        $title = ' Точный "SEO" \\ 😀 ';
        $description = 'Описание "quote" & details';
        LegacyFixture::$meta = ['_seo_master_meta_title' => $title, '_seo_master_meta_description' => $description];
        ob_start();
        seo_master_output_meta_tags();
        $html = ob_get_clean();
        return seo_master_filter_document_title('Original') === $title &&
            str_contains($html, 'content="' . esc_attr($description) . '"') && substr_count($html, '<meta ') === 1;
    },
    'legacy H1 metadata is never a title substitution' => function (): bool {
        LegacyFixture::$meta = ['_seo_master_h1' => 'Old managed H1'];
        return seo_master_filter_h1_title('Original heading', 7) === 'Original heading';
    },
];
foreach ($tests as $name => $test) {
    try {
        if (!$test()) { $failures[] = $name; }
    } catch (Throwable $error) {
        $failures[] = $name . ': ' . $error->getMessage();
    }
}
foreach ($failures as $failure) { fwrite(STDERR, "FAIL: $failure\n"); }
printf("legacy regressions: %d/%d passed (standalone WP stubs only)\n", count($tests) - count($failures), count($tests));
exit($failures === [] ? 0 : 1);
