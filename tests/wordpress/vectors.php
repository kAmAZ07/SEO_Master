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
$results = ['signing' => [], 'snapshots' => [], 'changes' => [], 'documents' => [], 'rollback' => []];
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
foreach ($vectors['rollback'] as $vector) {
    $identity = sm_v2_guard_identity($vector['original']);
    $command = $vector['command'];
    $command['proposal_hash'] = sm_v2_rollback_proposal_hash($command);
    $request_hash = hash('sha256', sm_v2_canonical($command));
    $guard = [
        'protocol' => 'seo-master/v2', 'kind' => 'rollback_guard', 'guard_id' => $identity['guard_id'],
        'identity_digest' => $identity['identity_digest'], 'references' => $identity['references'],
        'rollback_operation_id' => $command['operation_id'], 'rollback_request_hash' => $request_hash,
        'rollback_receipt_id' => sm_v2_receipt_id($command['operation_id'], $request_hash),
    ];
    $variant = $vector['original'];
    $variant['key_id'] = 'rotated-key';
    $variant['signature'] = 'not-part-of-identity';
    $variant['approval_ref'] = $command['approval_ref'];
    if (sm_v2_guard_identity($variant) !== $identity) { throw new RuntimeException('Guard identity depends on unstable metadata'); }
    $results['rollback'][] = [
        'identity' => $identity, 'proposal_hash' => $command['proposal_hash'],
        'guard_signature' => sm_v2_guard_signature($guard, $receipt_secret),
        'receipt_signature' => sm_v2_receipt_signature($guard, $receipt_secret),
    ];
}
foreach ($vectors['changes'] as $vector) {
    try { sm_v2_validate_changes($vector['value']); $results['changes'][] = true; }
    catch (SM_V2_Error $error) { $results['changes'][] = false; }
}
print(json_encode($results, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
