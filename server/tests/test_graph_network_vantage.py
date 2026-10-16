"""Synthetic graph assembly and validator checks. No live request occurs."""
import json
import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('NEO4J_PASSWORD', 'test-password')
os.environ.setdefault('OPENAI_API_KEY', 'test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX', '0')
import graph
import wiki
from network_vantage_qualifiers import NETWORK_VANTAGE_BODY_REASON, NETWORK_VANTAGE_REASON
from test_network_vantage_qualifiers import (
    BASE, DELIVERY, DELIVERY_LACK, OBSERVATION, ORIGIN, ORIGIN_LACK, QUESTION, WRONG,
)

SOURCE = '2026-10-16-120000-network-record'
NONCURRENT = ('Which approved network vantage is outside the Devbox for 21h.dev, '
              'and did capd deliver a safe client after October 14, 2026?')


def packet(bodies=None):
    bodies = {SOURCE: BASE} if bodies is None else bodies
    return {'facts': [], 'entities': [], 'source_excerpt': None,
            'semantic_notes': [{'name': name, 'content': text} for name, text in bodies.items()],
            'source_validation_texts': dict(bodies)}


def validate(answer, p=None, sources=None, question=QUESTION):
    return graph._validate_distilled_answer(
        {'answer': answer, 'confidence': 'high', 'sources': sources or [SOURCE], 'escalate': False},
        packet() if p is None else p, question,
    )


class NetworkVantageAnswerTests(unittest.IsolatedAsyncioTestCase):
    def assert_network_rejection(self, result, reason=NETWORK_VANTAGE_REASON):
        value, grounded, action = result
        self.assertEqual(action, 'network_vantage_mismatch')
        self.assertTrue(grounded)
        self.assertIsNone(value['answer'])
        self.assertEqual(value['confidence'], 'not_found')
        self.assertEqual(value['sources'], [])
        self.assertTrue(value['escalate'])
        self.assertEqual(value['superseded_note'], reason)

    def test_exact_question_and_wrong_compound_answer_use_new_guard(self):
        self.assert_network_rejection(validate(WRONG))
        self.assert_network_rejection(validate(WRONG, question=NONCURRENT))

    def test_destination_dns_and_direct_options_do_not_establish_origin(self):
        for route in ('curl --resolve edge-x', 'curl --connect-to edge-x', 'curl --doh-url dns-x',
                      'curl --noproxy *', 'curl --proxy remote-intent'):
            text = 'Use `' + route + '` with original Host/SNI as an outside-Devbox vantage.'
            p = packet({SOURCE: BASE + ' The Devbox network request used `' + route + '`.'})
            self.assert_network_rejection(validate(text, p))

    def test_scoped_negative_is_not_global_absence_or_native_authority(self):
        safe = ORIGIN_LACK + ' ' + DELIVERY_LACK
        result, grounded, action = validate(safe)
        self.assertEqual(result['answer'], safe)
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        self.assertTrue(grounded)
        self.assertEqual(action, 'downgraded')
        for tail in (' No method exists elsewhere.', ' Use it.', ' Native delivery is available.'):
            self.assert_network_rejection(validate(safe + tail))

    def test_explicit_independent_origin_control_keeps_route_and_date(self):
        result, _, action = validate(ORIGIN, packet({SOURCE: ORIGIN}))
        self.assertEqual(result['answer'], ORIGIN)
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        self.assertEqual(action, 'downgraded')
        self.assert_network_rejection(validate(ORIGIN.replace(', for this check only', ''), packet({SOURCE: ORIGIN})))
        self.assert_network_rejection(validate(ORIGIN.replace('`remote-host`', '`edge-x`'), packet({SOURCE: ORIGIN})))

    def test_observation_mode_does_not_transfer_from_another_check(self):
        doh = OBSERVATION.replace('`local-check`', '`doh-check`').replace('`ambient`', '`DoH`').replace('mode unknown', 'mode direct')
        p = packet({SOURCE: OBSERVATION + ' ' + doh})
        self.assertEqual(validate(OBSERVATION, p)[0]['answer'], OBSERVATION)
        self.assert_network_rejection(validate(OBSERVATION.replace('mode unknown', 'mode direct'), p))
        self.assert_network_rejection(validate('The original curl request used a proxy.', p))

    def test_delivery_needs_its_own_body_not_titles_or_network_sources(self):
        self.assert_network_rejection(validate(DELIVERY))
        p = packet({SOURCE: BASE})
        p['semantic_notes'][0]['description'] = DELIVERY
        self.assert_network_rejection(validate(DELIVERY, p))
        p['source_validation_texts'][SOURCE] = '# ' + DELIVERY + '\n\n' + BASE
        self.assert_network_rejection(validate(DELIVERY, p))
        p = packet({SOURCE: 'capd#9 remains blocked in an old source-only draft for 21h.dev.'})
        self.assert_network_rejection(validate('capd did not deliver a safe client after 2026-10-14.', p))
        self.assertEqual(validate(DELIVERY_LACK, p)[0]['answer'], DELIVERY_LACK)

    def test_compound_parts_and_capability_adjectives_need_matching_sources(self):
        p = packet({SOURCE: ORIGIN, SOURCE + '-other': DELIVERY.replace('an approved safe native', 'a safe')})
        self.assert_network_rejection(validate(ORIGIN + ' ' + DELIVERY, p, list(p['source_validation_texts'])))
        p = packet({SOURCE: ORIGIN, SOURCE + '-delivery': DELIVERY})
        self.assert_network_rejection(validate(ORIGIN.replace('`remote-check`', '`safe-client`'),
                                               p, list(p['source_validation_texts'])))
        p = packet({SOURCE: ORIGIN, SOURCE + '-delivery': DELIVERY})
        result = validate(ORIGIN + ' ' + DELIVERY, p, list(p['source_validation_texts']))[0]
        self.assertEqual(result['answer'], ORIGIN + ' ' + DELIVERY)
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])

    def test_every_raw_citation_before_five_source_display_cap(self):
        bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(7)}
        p = packet(bodies)
        result = validate(ORIGIN, p, list(bodies))[0]
        self.assertEqual(len(result['sources']), 5)
        self.assertEqual(result['confidence'], 'medium')
        for index in (5, 6):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[index]] += '\n`remote-check` is not approved for remote execution.'
            self.assert_network_rejection(validate(ORIGIN, p, list(bodies)))
        self.assert_network_rejection(validate(ORIGIN, packet(bodies), list(bodies) + ['unknown-source']),
                                      NETWORK_VANTAGE_BODY_REASON)

    def test_sixth_raw_metadata_and_code_scalar_keep_the_veto(self):
        bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
        contrary = '`remote-check` is not approved for an independent remote origin.'
        for raw in ('summary: >\n\n  ' + contrary, '```text\n' + contrary + '\n```',
                    '<pre>\n' + contrary + '\n</pre>', '# ' + contrary):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[-1]] = raw
            self.assert_network_rejection(validate(ORIGIN, p, list(bodies)))
        bodies = {SOURCE + '-' + str(i): BASE for i in range(6)}
        for raw in ('summary: >\n\n  ' + DELIVERY, '```text\n' + DELIVERY + '\n```'):
            p = packet(bodies)
            p['source_validation_texts'][list(bodies)[-1]] = raw
            self.assert_network_rejection(validate(DELIVERY_LACK, p, list(bodies)))

    def test_missing_private_bodies_fail_closed_on_every_citation(self):
        p = packet({SOURCE: ORIGIN})
        del p['source_validation_texts']
        self.assert_network_rejection(validate(ORIGIN, p), NETWORK_VANTAGE_BODY_REASON)
        bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
        for missing in ('', ' ', None):
            p = packet(bodies)
            if missing is None:
                del p['source_validation_texts'][list(bodies)[-1]]
            else:
                p['source_validation_texts'][list(bodies)[-1]] = missing
            self.assert_network_rejection(validate(ORIGIN, p, list(bodies)), NETWORK_VANTAGE_BODY_REASON)

    def test_full_draft_and_full_private_source_have_no_display_caps(self):
        long_answer = ORIGIN + ' ' + 'Recorded background. ' * 110 + WRONG
        self.assertGreater(long_answer.index(WRONG), 2000)
        self.assert_network_rejection(validate(long_answer, packet({SOURCE: ORIGIN})))
        contrary = '`remote-check` is not approved for remote execution.'
        body = ORIGIN + '\n\n' + 'Background context. ' * 250 + '\n' + contrary
        self.assertGreater(body.index(contrary), 4096)
        p = packet({SOURCE: ORIGIN})
        p['source_validation_texts'][SOURCE] = body
        self.assert_network_rejection(validate(ORIGIN, p))

    def test_mapping_keys_never_supply_support_or_veto(self):
        p = packet({SOURCE: ORIGIN})
        p['source_validation_texts'] = {ORIGIN: BASE}
        self.assert_network_rejection(validate(ORIGIN, p), NETWORK_VANTAGE_BODY_REASON)
        self.assertEqual(validate(ORIGIN, packet({SOURCE: ORIGIN}))[0]['answer'], ORIGIN)

    async def assemble(self, bodies, answer, question=QUESTION, sources=None, validation=None):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        hits = [{'name': name, 'score': .7 - index * .005} for index, name in enumerate(bodies)]
        validation_context = (mock.patch.object(graph, '_source_validation_context',
                                               new=mock.AsyncMock(return_value=validation))
                              if validation is not None else nullcontext())
        with validation_context, mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, 'FUSION', True), \
             mock.patch.object(wiki.idx, 'search', return_value=hits), \
             mock.patch.object(wiki.idx, 'get', side_effect=lambda name: {'body': bodies.get(name, '')}), \
             mock.patch.object(graph, '_distill', new=mock.AsyncMock(return_value={
                 'answer': answer, 'confidence': 'high', 'sources': sources or list(bodies), 'escalate': False,
             })) as distill, mock.patch.object(graph, '_record_query') as record:
            result = await graph._graph_answer_impl(question)
            assembled = await graph._assemble_packet(question, 8, current_state=graph._current_state_question(question), answer_evidence=True)
            search = await graph._assemble_packet(question, 3)
        self.assertIn('source_validation_texts', assembled)
        self.assertEqual(assembled['source_validation_texts'], bodies if validation is None else validation)
        self.assertNotIn('source_validation_texts', search)
        self.assertNotIn('PRIVATE_', json.dumps(search))
        self.assertNotIn('source_validation_texts', distill.call_args.args[1])
        return result, distill.call_args.args[1], record.call_args.args[0]

    async def test_real_mock_assembly_exact_question_reaches_new_guard(self):
        result, sent, event = await self.assemble({SOURCE: BASE}, WRONG)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertEqual(event['temporal_mode'], 'current')
        self.assertIsNone(result['answer'])
        self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        self.assertNotIn('source_validation_texts', sent)

    async def test_real_mock_client_options_and_ambient_mode_keep_the_new_route(self):
        for route in ('curl --resolve edge-x', 'curl --connect-to edge-x', 'curl --doh-url dns-x',
                      'curl --noproxy *'):
            answer = 'Use `' + route + '` as an outside-Devbox network vantage.'
            result, _, event = await self.assemble({SOURCE: BASE}, answer)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        doh = OBSERVATION.replace('`local-check`', '`doh-check`').replace('`ambient`', '`DoH`').replace('mode unknown', 'mode direct')
        body = OBSERVATION + ' ' + doh
        wrong = OBSERVATION.replace('mode unknown', 'mode direct')
        result, _, event = await self.assemble({SOURCE: body}, wrong)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])
        result, _, _ = await self.assemble({SOURCE: body}, OBSERVATION)
        self.assertIn('proxy mode unknown', result['answer'])
        self.assertEqual(result['confidence'], 'medium')

    async def test_real_mock_compound_parts_cannot_share_capability_or_origin(self):
        basic = DELIVERY.replace('an approved safe native', 'a safe')
        for answer in (ORIGIN + ' ' + DELIVERY, ORIGIN.replace('`remote-check`', '`safe-client`')):
            result, _, event = await self.assemble({SOURCE: ORIGIN + ' ' + basic}, answer)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])
        result, _, event = await self.assemble({SOURCE: ORIGIN + ' ' + DELIVERY}, ORIGIN + ' ' + DELIVERY)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])

    async def test_real_mock_late_draft_tail_is_checked_before_display(self):
        answer = ORIGIN + ' ' + 'Recorded background. ' * 110 + WRONG
        self.assertGreater(answer.index(WRONG), 2000)
        result, _, event = await self.assemble({SOURCE: ORIGIN}, answer)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])

    async def test_real_mock_missing_and_mapping_key_only_bodies_fail_closed(self):
        for private in ({SOURCE: ''}, {SOURCE: ' '}, {}, {ORIGIN: BASE}):
            result, _, event = await self.assemble({SOURCE: ORIGIN}, ORIGIN, validation=private)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_BODY_REASON)
            self.assertIsNone(result['answer'])

    async def test_real_mock_assembly_independent_origin_is_qualified(self):
        result, _, event = await self.assemble({SOURCE: ORIGIN}, ORIGIN)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        self.assertIn('for this check only', result['answer'])
        self.assertIn('Live state is not verified', result['answer'])
        self.assertIn('`curl --resolve edge-x`', result['answer'])

    async def test_private_sentinels_and_late_limits_do_not_enter_model_or_search_packets(self):
        sentinel = 'PRIVATE_NETWORK_BODY_SENTINEL'
        contrary = '`remote-check` is not approved for an independent remote origin.'
        body = '## Durable claims\n' + ORIGIN + '\n\n## Context\n' + 'Background context. ' * 260 + '\n' + sentinel + '\n' + contrary
        self.assertGreater(body.index(contrary), 4096)
        for question in (QUESTION, NONCURRENT):
            result, sent, event = await self.assemble({SOURCE: body}, ORIGIN, question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])
            self.assertNotIn(sentinel, sent)
            self.assertNotIn(sentinel, json.dumps(result))
            self.assertNotIn(contrary, sent)

    async def test_real_mock_sixth_raw_citation_vetoes_without_private_text_leak(self):
        sentinel = 'PRIVATE_SIXTH_NETWORK_SENTINEL'
        bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
        contrary = '`remote-check` is not approved for remote execution.'
        for raw in ('summary: >\n\n  ' + sentinel + '\n  ' + contrary,
                    '```text\n' + sentinel + '\n' + contrary + '\n```'):
            bodies[list(bodies)[-1]] = raw
            result, sent, event = await self.assemble(bodies, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])
            self.assertNotIn(sentinel, sent)
            self.assertNotIn(sentinel, json.dumps(result))

    async def test_real_mock_following_source_limits_keep_the_veto(self):
        body = ORIGIN + '\nBoth comparisons use the same Devbox, not independent external vantages.'
        result, _, event = await self.assemble({SOURCE: body}, ORIGIN)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        self.assertIsNone(result['answer'])

    async def test_real_mock_safe_negative_and_wrong_delivery_source(self):
        safe = ORIGIN_LACK + ' ' + DELIVERY_LACK
        result, _, event = await self.assemble({SOURCE: BASE}, safe)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
        result, _, event = await self.assemble({SOURCE: BASE}, DELIVERY)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])

    async def test_review_affirmative_local_origin_roles_use_private_raw_vetoes(self):
        contrary_clauses = (
            'On 2026-10-15, `remote-check` has caller origin `Devbox` for `21h.dev`.',
            'On 2026-10-15, `remote-check` executed for `21h.dev` on the Devbox.',
            'All observations for `21h.dev` originate from the Devbox.',
        )
        for contrary in contrary_clauses:
            p = packet({SOURCE: ORIGIN, SOURCE + '-local': contrary})
            self.assert_network_rejection(validate(ORIGIN, p, list(p['source_validation_texts'])))
            body = ORIGIN + '\n' + contrary
            result, _, event = await self.assemble({SOURCE: body}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        for unrelated in (contrary_clauses[0].replace('remote-check', 'local-check'),
                          contrary_clauses[1].replace('remote-check', 'local-check'),
                          '`local-check` has caller origin `Devbox` for `21h.dev` and uses the same Devbox.'):
            result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + unrelated}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertIsNotNone(result['answer'])
            self.assertEqual(result['confidence'], 'medium')

    async def test_review_local_origin_vetoes_survive_sixth_citations_and_long_bodies(self):
        contrary = 'On 2026-10-15, `remote-check` has caller origin `Devbox` for `21h.dev`.'
        sentinel = 'PRIVATE_ROLE_VALUE_SENTINEL'
        bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
        for raw in ('summary: >\n\n  ' + sentinel + '\n  ' + contrary,
                    '```text\n' + sentinel + '\n' + contrary + '\n```'):
            bodies[list(bodies)[-1]] = raw
            result, sent, event = await self.assemble(bodies, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            self.assertNotIn(sentinel, sent)
        body = ORIGIN + '\n' + 'Background context. ' * 260 + '\n' + sentinel + '\n' + contrary
        self.assertGreater(body.index(contrary), 4096)
        result, sent, event = await self.assemble({SOURCE: body}, ORIGIN)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        self.assertNotIn(sentinel, sent)
        answer = ORIGIN + ' ' + 'Recorded background. ' * 110 + contrary
        self.assertGreater(answer.index(contrary), 2000)
        result, _, event = await self.assemble({SOURCE: ORIGIN}, answer)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])

    async def test_review_requested_timestamp_cutoffs_fail_closed_in_private_assembly(self):
        old = DELIVERY.replace('2026-10-16', '2026-10-13')
        for cutoff in ('2026-10-14', '2026-10-14T00:00:00Z',
                       '2026-10-14T18:30:00+02:00', 'an unknown date', '14 October 2026', 'October 14th, 2026'):
            question = 'Did capd deliver a safe client for 21h.dev after ' + cutoff + '?'
            self.assert_network_rejection(validate(old, packet({SOURCE: old}), question=question))
            result, _, event = await self.assemble({SOURCE: old}, old, question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        history = 'Did capd deliver a safe client for 21h.dev?'
        result, _, event = await self.assemble({SOURCE: old}, old, history)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertEqual(result['answer'], old)
        same_day = DELIVERY.replace('2026-10-16', '2026-10-14')
        question = 'Did capd deliver a safe client for 21h.dev after 2026-10-14T00:00:00Z?'
        result, _, event = await self.assemble({SOURCE: same_day}, same_day, question)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])
        answer = ORIGIN + ' ' + 'Recorded background. ' * 110 + old
        result, _, event = await self.assemble({SOURCE: ORIGIN + ' ' + old}, answer, question)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])

    async def test_review_quoted_status_and_inside_origin_use_raw_private_vetoes(self):
        for contrary in ('`remote-check` is `not approved`.',
                         '`remote-check` has status `not approved`.',
                         '`remote-check` has caller origin `not independent` for `21h.dev`.',
                         '`remote-check` executes inside the Devbox.'):
            p = packet({SOURCE: ORIGIN + '\n' + contrary})
            self.assert_network_rejection(validate(ORIGIN, p))
            result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + contrary}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
            bodies[list(bodies)[-1]] = '```text\nPRIVATE_PROPERTY_SENTINEL\n' + ORIGIN + '\n' + contrary + '\n```'
            result, sent, event = await self.assemble(bodies, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            self.assertNotIn('PRIVATE_PROPERTY_SENTINEL', sent)
            body = ORIGIN + '\n' + 'Background context. ' * 250 + '\n' + contrary
            self.assertGreater(body.index(contrary), 4096)
            result, _, event = await self.assemble({SOURCE: body}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        for unrelated in ('`other-check` executes inside the Devbox.',
                          '`other-check` is `not approved`.',
                          'summary: `other-check` is `not approved`.'):
            result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + unrelated}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertIsNotNone(result['answer'])
        exact = ORIGIN.replace('remote-check', 'not approved').replace('curl --resolve edge-x', 'not independent')
        result, _, event = await self.assemble({SOURCE: exact}, exact)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertIn('`not approved`', result['answer'])
        self.assertIn('`not independent`', result['answer'])

    async def test_review_formatted_service_and_concept_collect_private_bodies(self):
        for question in ('Did `capd` deliver a safe client after October 14, 2026?',
                         'Can --resolve supply an independent `network vantage`?',
                         'Can `--resolve` supply an independent `network vantage`?'):
            self.assert_network_rejection(validate('This is approved.', question=question))
            result, _, event = await self.assemble({SOURCE: BASE}, 'This is approved.', question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            result, _, event = await self.assemble({SOURCE: BASE}, 'This is approved.', question,
                                                  validation={SOURCE: ''})
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_BODY_REASON)

    async def test_review_primary_target_for_and_checks_roles_cannot_transfer(self):
        other = ORIGIN.replace('21h.dev', 'other.dev')
        for question in ('Which approved network vantage outside the Devbox checks fixture.dev with Host/SNI other.dev?',
                         'Which approved network vantage outside the Devbox checks fixture.xyz?',
                         'What is the current approved network vantage outside the Devbox for fixture.dev with Host/SNI other.dev?',
                         'What is the current approved network vantage outside the Devbox for fixture.xyz with Host/SNI other.dev?',
                         'Which approved network vantage outside the Devbox checks an unknown target with DNS other.dev?'):
            self.assert_network_rejection(validate(other, packet({SOURCE: other}), question=question))
            result, _, event = await self.assemble({SOURCE: other}, other, question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        for role in ('Host/SNI', 'DNS', 'control', 'proxy', 'destination', 'method is'):
            question = 'What is the current approved network vantage outside the Devbox for fixture.xyz with ' + role + ' other.dev?'
            matched = other.replace('for `other.dev`', 'for `fixture.xyz`')
            result, _, event = await self.assemble({SOURCE: matched}, matched, question)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertIn('for `fixture.xyz`', result['answer'])
            self.assertEqual(result['confidence'], 'medium')
            self.assertTrue(result['escalate'])
        for target in ('https://Fixture.xyz/a?x=One.', 'Fixture.XYZ', '192.0.2.21', '[2001:db8::21]'):
            matched = ORIGIN.replace('for `21h.dev`', 'for `' + target + '`')
            question = 'Which approved network vantage outside the Devbox checks `' + target + '` with Host/SNI 21h.dev?'
            result, _, event = await self.assemble({SOURCE: matched}, matched, question)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertEqual(result['answer'], matched)
            result, _, event = await self.assemble({SOURCE: matched}, matched, question.replace('`' + target + '`', '`' + target + '.`'))
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])

    async def test_review_target_roles_cover_private_metadata_late_claims_and_sixth_sources(self):
        question = 'What is the current approved network vantage outside the Devbox for fixture.xyz with Host/SNI other.dev?'
        other = ORIGIN.replace('21h.dev', 'other.dev')
        for private in ('summary: >\n  ' + other, '```text\n' + other + '\n```'):
            result, _, event = await self.assemble({SOURCE: other}, other, question, validation={SOURCE: private})
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])
        bodies = {SOURCE + '-' + str(i): other for i in range(6)}
        bodies[list(bodies)[-1]] = 'summary: >\n  PRIVATE_TARGET_SENTINEL\n  ' + other
        result, sent, event = await self.assemble(bodies, other, question)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertNotIn('PRIVATE_TARGET_SENTINEL', sent)
        matched = other.replace('for `other.dev`', 'for `fixture.xyz`')
        answer = matched + ' ' + 'Recorded background. ' * 110 + other
        self.assertGreater(answer.index(other), 2000)
        result, _, event = await self.assemble({SOURCE: matched + ' ' + other}, answer, question)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])
        safe_bodies = {SOURCE + '-' + str(i): matched for i in range(6)}
        p = packet(safe_bodies)
        value, _, action = validate(matched, p, list(safe_bodies), question)
        self.assertEqual(action, 'downgraded')
        self.assertEqual(len(value['sources']), 5)
        self.assertEqual(value['confidence'], 'medium')

    async def test_review_target_binding_applies_to_other_statement_forms_and_scoped_negatives(self):
        question = 'Which network vantage outside the Devbox checks fixture.xyz with Host/SNI other.dev after October 14, 2026?'
        for statement in (DELIVERY.replace('21h.dev', 'other.dev'),
                          OBSERVATION.replace('21h.dev', 'other.dev'),
                          ORIGIN_LACK.replace('21h.dev', 'other.dev')):
            result, _, event = await self.assemble({SOURCE: statement}, statement, question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])
        body = BASE.replace('for 21h.dev', 'for other.dev') + ' Host/SNI fixture.xyz.'
        for answer in (DELIVERY_LACK, ORIGIN_LACK.replace('21h.dev', 'fixture.xyz')):
            result, _, event = await self.assemble({SOURCE: body}, answer, question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertIsNone(result['answer'])
        matched = BASE.replace('21h.dev', 'fixture.xyz')
        for answer in (DELIVERY_LACK, ORIGIN_LACK.replace('21h.dev', 'fixture.xyz')):
            result, _, event = await self.assemble({SOURCE: matched}, answer, question)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertEqual(result['confidence'], 'medium')

    async def test_review_short_local_origin_properties_use_only_bound_method_scope(self):
        for contrary in ('`remote-check` has caller origin `Devbox`.', '`remote-check` origin is `Devbox`.'):
            body = ORIGIN + '\n' + contrary
            self.assert_network_rejection(validate(ORIGIN, packet({SOURCE: body})))
            result, _, event = await self.assemble({SOURCE: body}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
            bodies[list(bodies)[-1]] = 'summary: >\n  PRIVATE_SHORT_ORIGIN_SENTINEL\n  ' + ORIGIN + '\n  ' + contrary
            result, sent, event = await self.assemble(bodies, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertNotIn('PRIVATE_SHORT_ORIGIN_SENTINEL', sent)
            for other in (contrary.replace('remote-check', 'other-check'),
                          contrary.rstrip('.') + ' for `other.dev`.'):
                result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + other}, ORIGIN)
                self.assertEqual(event['grounding_action'], 'downgraded')
                self.assertIsNotNone(result['answer'])

    async def test_review_whole_cutoff_and_origin_concept_formats_keep_private_validation(self):
        old = DELIVERY.replace('2026-10-16', '2026-10-13')
        for cutoff in ('`after October 14, 2026`', '`after 2026-10-14T00:00:00Z`',
                       '`since an unknown date`'):
            question = 'Did capd deliver a safe client for 21h.dev ' + cutoff + '?'
            self.assert_network_rejection(validate(old, packet({SOURCE: old}), question=question))
            result, _, event = await self.assemble({SOURCE: old}, old, question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        question = 'Which `independent remote origin` is approved for 21h.dev?'
        result, _, event = await self.assemble({SOURCE: BASE}, WRONG, question)
        self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        self.assertIsNone(result['answer'])
        result, _, event = await self.assemble({SOURCE: ORIGIN}, ORIGIN, question)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertEqual(result['answer'], ORIGIN)
        self.assertTrue(result['escalate'])
        for method in ('after October 14, 2026', 'after', 'curl --data "after October 14, 2026"'):
            historical = old.replace('safe-client', method)
            question = 'Did capd deliver `' + method + '` as a safe client for 21h.dev?'
            result, _, event = await self.assemble({SOURCE: historical}, historical, question)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertEqual(result['answer'], historical)

    async def test_residual_property_limits_use_role_aware_unknown_refusal(self):
        for contrary in ('`remote-check` was `not executed` for `21h.dev`.',
                         '`remote-check` has status `not observed` for `21h.dev`.',
                         '`remote-check` has status `unrecognized-status` for `21h.dev`.',
                         '`remote-check` has status `not approved for 21h.dev`.'):
            for question in (QUESTION, NONCURRENT):
                body = ORIGIN + '\n' + contrary
                self.assert_network_rejection(validate(ORIGIN, packet({SOURCE: body}), question=question))
                result, _, event = await self.assemble({SOURCE: body}, ORIGIN, question)
                self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
                self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            for raw in ('summary: >\n  PRIVATE_STATUS_SENTINEL\n  ' + contrary,
                        '```text\nPRIVATE_STATUS_SENTINEL\n' + contrary + '\n```'):
                bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
                bodies[list(bodies)[-1]] = raw
                result, sent, event = await self.assemble(bodies, ORIGIN)
                self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
                self.assertNotIn('PRIVATE_STATUS_SENTINEL', sent)
            body = ORIGIN + '\n' + 'Background context. ' * 250 + '\n' + contrary
            self.assertGreater(body.index(contrary), 4096)
            result, _, event = await self.assemble({SOURCE: body}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            answer = ORIGIN + ' ' + 'Recorded background. ' * 110 + contrary
            self.assertGreater(answer.index(contrary), 2000)
            result, _, event = await self.assemble({SOURCE: ORIGIN}, answer)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
        for limit in ('`other-check` was `not executed` for `21h.dev`.',
                      '`remote-check` was `not executed` for `other.dev`.',
                      '`remote-check` route is `not executed`.'):
            result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + limit}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertIsNotNone(result['answer'])

    async def test_residual_multiple_cutoff_constraints_fail_closed_in_both_modes(self):
        delivered = DELIVERY.replace('2026-10-16', '2026-10-15')
        for tail in ('after October 14, 2026 and after October 17, 2026',
                     'after October 14, 2026 and since an unknown date',
                     '`after October 14, 2026 and after October 17, 2026`'):
            for prefix in ('Did capd deliver a safe client for 21h.dev ',
                           'What is the current network vantage outside the Devbox for 21h.dev and did capd deliver a safe client '):
                question = prefix + tail + '?'
                self.assert_network_rejection(validate(delivered, packet({SOURCE: delivered}), question=question))
                result, _, event = await self.assemble({SOURCE: delivered}, delivered, question)
                self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
                self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        question = 'Did capd deliver a safe client for 21h.dev after October 14, 2026?'
        result, _, event = await self.assemble({SOURCE: delivered}, delivered, question)
        self.assertEqual(event['grounding_action'], 'downgraded')
        self.assertEqual(result['answer'], delivered)

    async def test_residual_unbound_quoted_network_values_cannot_remove_a_target(self):
        other = ORIGIN.replace('21h.dev', 'other.dev')
        for target in ('fixture.xyz', 'https://fixture.xyz/a?x=1', '192.0.2.21', '[2001:db8::21]'):
            for qualifier in ('approved', 'current approved'):
                question = 'Which ' + qualifier + ' network vantage outside the Devbox can reach `' + target + '`?'
                self.assert_network_rejection(validate(other, packet({SOURCE: other}), question=question))
                result, _, event = await self.assemble({SOURCE: other}, other, question)
                self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
                self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
        statement = ORIGIN.replace('remote-check', 'method.fixture.xyz')
        for question in ('Which approved network vantage outside the Devbox uses `method.fixture.xyz` for 21h.dev?',
                         'Can `method.fixture.xyz` supply an independent network vantage for 21h.dev?'):
            result, _, event = await self.assemble({SOURCE: statement}, statement, question)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertIsNotNone(result['answer'])

    async def test_residual_following_origin_declarations_keep_only_the_same_scope(self):
        for contrary in ('The caller origin is `Devbox`.', 'The origin is `Devbox`.'):
            for question in (QUESTION, NONCURRENT):
                result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + contrary}, ORIGIN, question)
                self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
                self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            bodies = {SOURCE + '-' + str(i): ORIGIN for i in range(6)}
            bodies[list(bodies)[-1]] = 'summary: >\n  ' + ORIGIN + '\n  ' + contrary
            result, _, event = await self.assemble(bodies, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            body = ORIGIN + '\n' + 'Background context. ' * 260 + '\nPRIVATE_ANAPHORA_SENTINEL\n' + contrary
            self.assertGreater(body.index(contrary), 4096)
            result, sent, event = await self.assemble({SOURCE: body}, ORIGIN)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            self.assertNotIn('PRIVATE_ANAPHORA_SENTINEL', sent)
            for other in ('`other-check` executed for `other.dev` on the Devbox. ' + contrary,
                          contrary.rstrip('.') + ' for `other.dev`.'):
                result, _, event = await self.assemble({SOURCE: ORIGIN + '\n' + other}, ORIGIN)
                self.assertEqual(event['grounding_action'], 'downgraded')
                self.assertIsNotNone(result['answer'])

    async def test_residual_quoted_concept_use_questions_always_collect_private_text(self):
        for question in ('Which `network vantage` can I use for 21h.dev?',
                         'Which current `network vantage` can I use for 21h.dev?',
                         'What is the current `network vantage` I can use for 21h.dev?'):
            self.assert_network_rejection(validate('This is approved.', question=question))
            result, _, event = await self.assemble({SOURCE: BASE}, 'This is approved.', question)
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_REASON)
            result, _, event = await self.assemble({SOURCE: BASE}, 'This is approved.', question, validation={SOURCE: ''})
            self.assertEqual(event['grounding_action'], 'network_vantage_mismatch')
            self.assertEqual(result['superseded_note'], NETWORK_VANTAGE_BODY_REASON)
            result, _, event = await self.assemble({SOURCE: ORIGIN}, ORIGIN, question)
            self.assertEqual(event['grounding_action'], 'downgraded')
            self.assertIsNotNone(result['answer'])

    async def test_unrelated_question_never_loads_private_source_context(self):
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph, '_get_graph', new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph, '_resolve_episode_slugs', new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph, '_wiki_semantic_hits', new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(graph, '_source_validation_context', new=mock.AsyncMock()) as full:
            for question in ('What is the current Devbox version?', 'What is the capd version?', 'How does DNS work?',
                             'Where is `network vantage`?', 'What is the `capd` version?',
                             'What is the `outside-Devbox` version?', 'Where is `independent remote origin`?'):
                p = await graph._assemble_packet(question, 3, answer_evidence=True)
                self.assertNotIn('source_validation_texts', p)
        full.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
