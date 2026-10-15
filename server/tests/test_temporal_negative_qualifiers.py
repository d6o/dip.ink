"""Synthetic cutoff fixtures. No service calls or external receipt checks."""
import unittest
from temporal_negative_qualifiers import (
    TEMPORAL_NEGATIVE_REASON, temporal_negative_problem, temporal_negative_required,
)

CUTOFF = '2026-10-13T02:37:29Z'
QUESTION = ('Is there a new verified independent off-cluster age recipient, protected '
            'restore-identity path, or custody receipt for the Infisical PostgreSQL backup after ' + CUTOFF + '?')
WRONG = 'No new verified independent custody receipt exists after ' + CUTOFF + '.'
SAFE = 'The retrieved dated evidence contains no qualifying custody receipt after ' + CUTOFF + '.'
OLD = 'As of 2026-10-11T12:05:54Z, independent backup custody remains unverified.'
LATER = 'As of 2026-10-14T12:00:00Z, a new independent custody receipt is verified.'
LIMITED = 'As of 2026-10-14T12:00:00Z, the supplied backup custody evidence has no verified receipt.'


class TemporalNegativeTests(unittest.TestCase):
    def test_explicit_cutoff_receipt_intent(self):
        self.assertTrue(temporal_negative_required(QUESTION))
        self.assertTrue(temporal_negative_required(QUESTION.replace('after ', 'AFTER')))
        for question in ('What is a backup?', 'What changed after 2026-10-13?',
                         'Does backup custody exist?', 'Is there a receipt after 2026-99-99?'):
            self.assertFalse(temporal_negative_required(question))
            self.assertIsNone(temporal_negative_problem(question, WRONG, []))

    def reject(self, answer, texts):
        self.assertEqual(temporal_negative_problem(QUESTION, answer, texts), TEMPORAL_NEGATIVE_REASON)

    def test_old_current_fact_cannot_establish_universal_absence(self):
        self.reject(WRONG, [OLD])
        self.reject('No.', [OLD])
        self.reject('Independent custody remains unverified after ' + CUTOFF + '.', [OLD])
        for prefix in ('Zero new custody receipts arrived after ', '0 new custody receipts arrived after ',
                       'A new custody receipt remains to be verified after '):
            self.reject(prefix + CUTOFF + '.', [OLD])

    def test_later_timestamp_alone_never_establishes_complete_coverage(self):
        self.reject(WRONG, [LIMITED])
        self.reject(WRONG, [LATER])
        self.reject(WRONG, ['captured: 2026-10-14T12:00:00Z\n' + OLD])

    def test_empty_or_clear_queues_do_not_support_negative(self):
        for texts in ([], [''], ['All backup ingest queues are clear as of 2026-10-14T12:00:00Z.'],
                      ['As of 2026-10-14T12:00:00Z, no backup ingest backlog exists.']):
            self.reject(WRONG, texts)
            self.reject(SAFE, texts)

    def test_explicit_retrieved_evidence_limit_with_dated_old_observation(self):
        self.assertIsNone(temporal_negative_problem(QUESTION, SAFE, [OLD]))
        self.assertIsNone(temporal_negative_problem(QUESTION, SAFE, [LIMITED]))

    def test_informal_limit_does_not_hide_universal_absence(self):
        for text in ('Based on retrieved evidence, ' + WRONG,
                     'The retrieved evidence proves ' + WRONG,
                     SAFE + ' No new receipt exists anywhere.',
                     SAFE[:-1] + ' and none exists anywhere.',
                     SAFE + ' Custody remains unverified.'):
            self.reject(text, [OLD])

    def test_changed_or_missing_cutoff_fails_closed(self):
        self.reject(SAFE.replace(CUTOFF, '2026-10-14T00:00:00Z'), [OLD])
        self.reject('The retrieved evidence contains no qualifying custody receipt.', [OLD])

    def test_positive_after_cutoff_defeats_retrieved_absence(self):
        self.reject(SAFE, [OLD, LATER])
        self.reject(SAFE, [OLD + '\n' + LATER])
        self.reject(SAFE, [OLD] * 5 + [LATER])
        self.reject(SAFE, [LATER[:-1] + ', with no change to the separate backup hold.'])
        self.reject(SAFE, [LATER[:-1] + ', with an unknown separate backup status.'])

    def test_body_observation_not_source_slug_or_metadata_date(self):
        self.reject(SAFE, {'2026-10-11-120554-backup': 'Backup custody remains unverified.'})
        self.reject(SAFE, ['captured: 2026-10-14T12:00:00Z\nBackup custody remains unverified.'])
        self.reject(SAFE, ['As of 2026-10-14T12:00:00Z, the backup has metadata.'])
        self.reject(SAFE, ['{"updated_at": "2026-10-14T12:00:00Z", "custody": "unverified"}'])
        self.reject(SAFE, ['The metadata date is 2026-10-14T12:00:00Z; custody is unverified.'])

    def test_mapping_values_not_keys(self):
        self.assertIsNone(temporal_negative_problem(QUESTION, SAFE, {'source': OLD}))
        self.reject(SAFE, {OLD: 'No dated observation.'})
        self.reject(SAFE, {'one': OLD, 'two': LATER})

    def test_positive_answers_are_outside_negative_guard(self):
        self.assertIsNone(temporal_negative_problem(QUESTION, LATER, [LATER]))

    def test_static_reason_does_not_echo_private_text(self):
        self.reject(WRONG, ['synthetic-private-placeholder'])
        self.assertNotIn('synthetic', TEMPORAL_NEGATIVE_REASON)


if __name__ == '__main__':
    unittest.main()
