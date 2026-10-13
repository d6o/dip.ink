"""Service-operation boundaries, without reading credentials or contacting a vault."""
import os
import unittest
from unittest import mock

os.environ.setdefault('NEO4J_PASSWORD','test-password')
os.environ.setdefault('OPENAI_API_KEY','test-only')
os.environ.setdefault('WIKI_MCP_BACKGROUND_REINDEX','0')
import graph
import wiki

QUESTION = 'What is the current approved exact-User-Agent method for live Infisical credential reads in Devbox, and what remains blocked for devbox#14?'
CLIENT = '2026-10-12-230447-devbox-exact-ua-metadata-reader'
AUDIT = '2026-10-13-155127-infisical-user-agent-audit-gates'
DRAFT = '2026-10-13-203056-infisical-source-draft-review-boundary'
# Safe bounded source facts from the ticket and notes. No credential values,
# profiles, kubeconfig contents, private credential loader, or live requests.
METADATA = 'Devbox installs devbox-kube-read for narrow Kubernetes workload metadata. It supports no Secret reads.'
SOURCE_ONLY = 'Infisical has a reviewed source draft only. No approved installed method for live Infisical credential reads exists. Native delivery and live header proof remain absent.'
WRONG = 'The approved installed method for live Infisical credential reads is devbox-kube-read with its owned kubeconfig. No live Infisical header method is approved yet.'


def packet(include_source=True):
    return {'facts':[{'source_slug':CLIENT,'fact':METADATA,'current':True}],
            'entities':[{'name':'devbox-kube-read','summary':METADATA}],
            'source_excerpt':{'slug':CLIENT,'content':METADATA},
            'semantic_notes':[{'name':DRAFT,'content':SOURCE_ONLY}] if include_source else []}


class CredentialMethodAnswerPathTests(unittest.IsolatedAsyncioTestCase):
    def test_plain_service_name_is_detected_for_credential_method_question(self):
        self.assertIn('infisical',graph._answer_subjects(QUESTION))
        self.assertNotIn('devbox',graph._answer_subjects(QUESTION))

    async def test_exact_question_rejects_metadata_tool_transfer(self):
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=packet())), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer':WRONG,'confidence':'high','sources':[DRAFT],'escalate':False,
             })), mock.patch.object(graph,'_record_query'):
            result=await graph._graph_answer_impl(QUESTION)
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    async def test_no_service_specific_evidence_abstains_before_distillation(self):
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=packet(False))), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock()) as distill, \
             mock.patch.object(graph,'_record_query'):
            result=await graph._graph_answer_impl(QUESTION)
        self.assertEqual(result['confidence'],'not_found')
        self.assertTrue(result['escalate'])
        distill.assert_not_awaited()

    async def test_current_hydration_prefers_service_note_over_newer_ua_metadata(self):
        hits=[{'name':'2026-10-13-230000-kubernetes-metadata','score':.7,'description':'Installed exact-UA client'},
              {'name':'2026-10-13-220000-kubernetes-reader','score':.69},
              {'name':'2026-10-13-210000-workload-client','score':.68},
              {'name':DRAFT,'score':.67,'description':'Infisical source draft, not installed'}]
        with mock.patch.object(graph,'FUSION',True), \
             mock.patch.object(wiki.idx,'search',return_value=hits), \
             mock.patch.object(wiki.idx,'get',return_value={'body':SOURCE_ONLY}) as get:
            out=await graph._wiki_semantic_hits(QUESTION,25,hydrate=True,answer_evidence=True)
        self.assertEqual(get.call_args_list[0].args[0],DRAFT)
        self.assertTrue(next(h for h in out if h['name']==DRAFT).get('content'))

    async def test_safe_absence_does_not_recommend_any_reader(self):
        text='No approved installed method for live Infisical credential reads exists. Devbox#14 remains blocked on native delivery.'
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=packet())), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer':text,'confidence':'high','sources':[DRAFT],'escalate':False,
             })), mock.patch.object(graph,'_record_query'):
            result=await graph._graph_answer_impl(QUESTION)
        if result.get('answer'):
            self.assertIn('No approved installed method',result['answer'])
            self.assertNotIn('devbox-kube-read',result['answer'])
        self.assertTrue(result['escalate'])


    async def test_supported_positive_fixture_remains_recorded_and_escalates(self):
        query = 'What is the approved exact-User-Agent method for live VaultDesk credential reads?'
        source = '2026-10-13-120000-vaultdesk-reader'
        body = '`vault-reader` is the supported installed live exact-User-Agent method for VaultDesk credential reads.'
        p = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': source, 'content': body}]}
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer': 'Use `vault-reader` for live VaultDesk credential reads.',
                 'confidence': 'high', 'sources': [source], 'escalate': False,
             })), mock.patch.object(graph,'_record_query'):
            result = await graph._graph_answer_impl(query)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['sources'], [source])
        self.assertEqual(result['confidence'], 'medium')
        self.assertTrue(result['escalate'])
