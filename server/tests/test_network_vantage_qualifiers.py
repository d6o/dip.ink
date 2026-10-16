"""Synthetic origin and delivery checks. No network request occurs."""
import unittest

from network_vantage_qualifiers import (
    NETWORK_VANTAGE_BODY_REASON, NETWORK_VANTAGE_REASON,
    network_vantage_problem, network_vantage_required,
)

QUESTION = ('What is the current approved way to obtain a network vantage outside the Devbox for 21h.dev, '
            'and did capd deliver a safe client or network diagnostic method after October 14, 2026?')
BASE = ('On 2026-10-15, two network comparisons for 21h.dev use the same Devbox, not independent external vantages. '
        'A forced destination edge succeeds with original Host/SNI. Normal DNS recovery remains unverified. '
        'Original curl direct/proxy mode is unknown. A separate DoH request uses explicit direct mode. '
        'Caller-local route tables do not establish an independent vantage.')
ORIGIN = ('On 2026-10-16, `remote-check` is an approved network check for `21h.dev` '
          'from independent remote origin `remote-host` outside the Devbox via `curl --resolve edge-x`, '
          'with Host/SNI `21h.dev`, with observed execution at `remote-host`, for this check only.')
DELIVERY = ('On 2026-10-16, capd delivered `safe-client` as an approved safe native client '
            'for `21h.dev` with observed successful invocation of `safe-client`.')
OBSERVATION = ('On 2026-10-15, `local-check` observed HTTP 403 for `21h.dev` from the Devbox '
               'with destination `edge-x`, DNS `ambient`, proxy mode unknown, and Host/SNI `21h.dev`.')
ORIGIN_LACK = 'The cited network records do not establish an approved outside-Devbox origin for `21h.dev`.'
DELIVERY_LACK = ('The cited network records do not establish capd delivery of a safe client or network '
                 'diagnostic method after 2026-10-14.')
WRONG = ('Use an explicit edge via `curl --resolve edge-x` with original Host/SNI as an outside-Devbox '
         'vantage. capd did not deliver a safe client after October 14, 2026.')


class NetworkVantageTests(unittest.TestCase):
    def problem(self, answer, bodies=None, question=QUESTION):
        return network_vantage_problem(question, answer, [BASE] if bodies is None else bodies)

    def reject(self, answer, bodies=None):
        self.assertEqual(self.problem(answer, bodies), NETWORK_VANTAGE_REASON)

    def test_exact_question_selects_and_exact_wrong_answer_fails(self):
        self.assertTrue(network_vantage_required(QUESTION))
        self.reject(WRONG)

    def test_scoped_held_record_negatives_do_not_claim_global_absence(self):
        safe = ORIGIN_LACK + ' ' + DELIVERY_LACK
        self.assertIsNone(self.problem(safe))
        self.assertIsNone(self.problem(safe.replace('cited', 'held')))
        for tail in (' Therefore no outside origin exists.', ' capd did not deliver it.',
                     ' Use it.', ' This grants native access.', ' No method exists anywhere.'):
            self.reject(safe + tail)
        self.reject('No independent external network vantage exists.')
        self.reject('capd did not deliver a safe client after 2026-10-14.')

    def test_client_and_destination_options_do_not_supply_origin(self):
        for route in ('curl --resolve edge-x', 'curl --connect-to edge-x', 'curl --doh-url dns-x',
                      'curl --noproxy *', 'curl --proxy remote-intent', 'DoH direct mode'):
            body = 'On 2026-10-16, 21h.dev succeeds from the Devbox via `' + route + '` with original Host/SNI.'
            wrong = 'Use `' + route + '` as an independent outside-Devbox origin for `21h.dev`.'
            self.reject(wrong, [body])
            self.assertIsNone(self.problem(ORIGIN_LACK, [body]))

    def test_explicit_observed_remote_origin_preserves_route_qualification(self):
        self.assertIsNone(self.problem(ORIGIN, [ORIGIN]))
        self.assertIsNone(self.problem(ORIGIN + ' ' + DELIVERY, [ORIGIN, DELIVERY]))
        for route in ('curl --connect-to edge-x', 'curl --doh-url dns-x', 'remote-proxy-check'):
            statement = ORIGIN.replace('curl --resolve edge-x', route)
            self.assertIsNone(self.problem(statement, [statement]))
        for removed in (', for this check only', ', with Host/SNI `21h.dev`',
                        ', with observed execution at `remote-host`', ' from independent remote origin `remote-host`'):
            self.reject(ORIGIN.replace(removed, ''), [ORIGIN])
        self.reject(ORIGIN.replace('`remote-host`', '`Devbox`'), [ORIGIN.replace('`remote-host`', '`Devbox`')])

    def test_unknown_ambient_mode_does_not_take_direct_mode_from_doh(self):
        doh = OBSERVATION.replace('`local-check`', '`doh-check`').replace('`ambient`', '`DoH`').replace('mode unknown', 'mode direct')
        self.assertIsNone(self.problem(OBSERVATION, [OBSERVATION, doh]))
        self.reject(OBSERVATION.replace('mode unknown', 'mode direct'), [OBSERVATION, doh])
        self.reject('The original curl request used direct mode.', [OBSERVATION, doh])
        self.reject('The DoH success proves that the original curl request used a proxy.', [OBSERVATION, doh])

    def test_observed_access_does_not_establish_approval_or_delivery(self):
        self.reject(ORIGIN, [OBSERVATION])
        self.reject(DELIVERY, [OBSERVATION])
        self.reject('The observed HTTP 403 authorizes a network diagnostic.', [OBSERVATION])

    def test_capd_delivery_requires_body_dated_actual_use(self):
        self.assertIsNone(self.problem(DELIVERY, [DELIVERY]))
        for body in (BASE, 'capd#9 remains blocked in a source-only draft.',
                     'On 2026-10-16, capd plans a safe client.',
                     DELIVERY.replace(' with observed successful invocation of `safe-client`', ''),
                     DELIVERY.replace('2026-10-16', '2026-10-13')):
            self.reject(DELIVERY, [body])
        self.reject(DELIVERY.replace('2026-10-16', '2026-10-13'), [DELIVERY.replace('2026-10-16', '2026-10-13')])

    def test_capability_and_identity_do_not_transfer_across_sources(self):
        basic = DELIVERY.replace('an approved safe native', 'a safe')
        self.reject(DELIVERY, [basic, '`other-client` is approved and native.'])
        self.assertIsNone(self.problem(basic, [basic]))
        self.reject(ORIGIN + ' ' + DELIVERY, [ORIGIN, 'On 2026-10-16, a network check succeeded.'])
        self.reject(ORIGIN.replace('`remote-check`', '`safe-client`'), [ORIGIN, DELIVERY])
        self.reject(ORIGIN.replace('`remote-check`', '`Remote-check`'), [ORIGIN])
        self.reject(ORIGIN.replace('`curl --resolve edge-x`', '`curl --connect-to edge-x`'), [ORIGIN])

    def test_literal_identity_and_negation_remain_separate(self):
        exact = ORIGIN.replace('remote-check', '/opt/doesn\u2019t*Client')
        self.assertIsNone(self.problem(exact, [exact]))
        self.reject(exact.replace('\u2019', "'"), [exact])
        self.reject(exact.replace('*', ''), [exact])
        contrary = '`/opt/doesn\u2019t*Client` isn\u2019t approved for an independent remote origin.'
        self.reject(exact, [exact, contrary])

    def test_mapping_keys_never_supply_evidence(self):
        self.reject(ORIGIN, {ORIGIN: BASE})
        self.assertIsNone(self.problem(ORIGIN, {'key': ORIGIN}))
        self.assertIsNone(self.problem(ORIGIN, {ORIGIN + ' not approved': ORIGIN}))

    def test_metadata_code_and_titles_do_not_supply_support(self):
        for raw in ('# ' + ORIGIN, 'summary: >\n\n  ' + ORIGIN,
                    '---\nsummary: ' + ORIGIN + '\n---', '```text\n' + ORIGIN + '\n```',
                    '<pre>\n' + ORIGIN + '\n</pre>', '## Example\n\n' + ORIGIN):
            self.reject(ORIGIN, [raw])
        for raw in ('summary: >\n\n  ' + DELIVERY, '```text\n' + DELIVERY + '\n```'):
            self.reject(DELIVERY, [raw])
            self.reject(DELIVERY_LACK, [BASE, raw])

    def test_raw_limits_veto_after_body_filters_and_long_prefixes(self):
        contrary = '`remote-check` is not approved for an independent remote origin.'
        for raw in ('summary: >\n\n  ' + contrary, '```text\n' + contrary + '\n```',
                    '# ' + contrary, '<!--\n' + contrary + '\n-->'):
            self.reject(ORIGIN, [ORIGIN] * 5 + [raw])
        long_body = ORIGIN + '\n\n' + 'Background context. ' * 250 + '\n' + contrary
        self.assertGreater(long_body.index(contrary), 4096)
        self.reject(ORIGIN, [long_body])
        self.reject(ORIGIN + ' ' + 'Recorded background. ' * 110 + WRONG, [ORIGIN])

    def test_source_local_following_limits_veto_without_positive_transfer(self):
        for tail in ('It is not approved for remote execution.',
                     'Both comparisons use the same Devbox, not independent external vantages.',
                     'The origin is unknown.', 'Source-only draft.'):
            self.reject(ORIGIN, [ORIGIN + '\n' + tail])
        self.reject(ORIGIN, [ORIGIN, 'Network records for 21h.dev.\nBoth comparisons use the same Devbox.'])

    def test_raw_delivery_candidate_vetoes_scoped_negative_without_proving_delivery(self):
        for candidate in (DELIVERY, 'summary: >\n  ' + DELIVERY,
                          'On 2026-10-16, capd delivered a safe client.', 'capd delivered a safe client.',
                          'On 2026-10-16, capd delivered a safe client but not a remote origin.'):
            self.reject(DELIVERY_LACK, [BASE, candidate])
        old = DELIVERY.replace('2026-10-16', '2026-10-13')
        self.assertIsNone(self.problem(DELIVERY_LACK, [BASE, old]))
        self.assertIsNone(self.problem(DELIVERY_LACK, [BASE, 'capd#9 is blocked in an old source-only draft.']))

    def test_missing_bodies_and_unknown_assertions_fail_closed(self):
        for bodies in ([], [''], [BASE, ' '], {'source': ''}):
            self.assertEqual(self.problem(ORIGIN_LACK, bodies), NETWORK_VANTAGE_BODY_REASON)
        for text in ('Yes.', 'No.', 'This is approved.', 'The current method is available.',
                     ORIGIN_LACK + ' Native delivery is enabled.'):
            self.reject(text)

    def test_related_origin_and_delivery_questions_select(self):
        for question in ('Which external vantages are approved?',
                         'Did capd deliver a client after 2026-10-14?',
                         'Did capd provide a safe network client?',
                         'Which independent origins are outside the Devbox?'):
            self.assertTrue(network_vantage_required(question))

    def test_unrelated_questions_do_not_select(self):
        for question in ('What is the current Devbox version?', 'How does DNS work?',
                         'Where is `network vantage`?', 'What is the capd version?',
                         'What is the `outside-Devbox` version?'):
            self.assertFalse(network_vantage_required(question))
            self.assertIsNone(self.problem('Unchanged answer.', [], question))

    def test_iso_cutoff_and_scope_must_match(self):
        question = 'Did capd deliver a safe client after 2026-10-14?'
        self.assertIsNone(self.problem(DELIVERY_LACK, [BASE], question))
        self.assertEqual(self.problem(DELIVERY_LACK.replace('10-14', '10-13')), NETWORK_VANTAGE_REASON)
        self.reject(ORIGIN_LACK.replace('21h.dev', 'other.dev'))


if __name__ == '__main__':
    unittest.main()
