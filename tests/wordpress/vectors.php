<?php
declare(strict_types=1);
define('ABSPATH', __DIR__);
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-protocol.php';

$secret = getenv('SM_TEST_HMAC_KEY');
$receipt_secret = getenv('SM_TEST_RECEIPT_KEY');
if (!is_string($secret) || strlen($secret) !== 64 || !is_string($receipt_secret) || strlen($receipt_secret) !== 64) {
    fwrite(STDERR, "Ephemeral test keys required (never print them).\n");
    exit(1);
}
$vectors = json_decode(stream_get_contents(STDIN), true, 32, JSON_THROW_ON_ERROR);
$results = ['signing' => [], 'snapshots' => [], 'changes' => [], 'documents' => []];
foreach ($vectors['documents'] as $vector) {
    try {
        $document = sm_v2_decode($vector['body']);
        sm_v2_validate_changes($document['changes']);
        $results['documents'][] = true;
    } catch (SM_V2_Error $error) { $results['documents'][] = false; }
}
foreach ($vectors['signing'] as $vector) {
    $message = sm_v2_signing_message($vector['method'], $vector['route'], $vector['body'], $vectors['headers']);
    $results['signing'][] = [
        'body_digest' => hash('sha256', $vector['body']), 'message' => $message,
        'signature' => hash_hmac('sha256', $message, $secret),
    ];
}
foreach ($vectors['snapshots'] as $snapshot) {
    $results['snapshots'][] = [
        'canonical' => sm_v2_canonical($snapshot), 'digest' => sm_v2_digest($snapshot),
        'receipt_signature' => sm_v2_receipt_signature($snapshot, $receipt_secret),
    ];
}
foreach ($vectors['changes'] as $vector) {
    try { sm_v2_validate_changes($vector['value']); $results['changes'][] = true; }
    catch (SM_V2_Error $error) { $results['changes'][] = false; }
}
print(json_encode($results, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
