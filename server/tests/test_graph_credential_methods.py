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
        self.assertIsNotNone(result.get('answer'))
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


class FullCredentialSourceContradictionTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = '2026-10-13-120000-infisical-method-source'
    POSITIVE = '`vault-reader` is the supported installed live exact-User-Agent method for Infisical credential reads.'
    NEGATIVE = 'Infisical has no approved installed live credential-read method.'
    DRAFT = 'Use `vault-reader` for live Infisical credential reads.'

    async def test_long_source_contradiction_rejects_actual_answer_without_model_expansion(self):
        from types import SimpleNamespace
        body = '## Durable claims\n\n' + self.POSITIVE + '\n\n## Context\n\n' + ('Background context. ' * 180) + '\n\n' + self.NEGATIVE
        self.assertGreater(len(body), 2500)
        g = SimpleNamespace(search_=mock.AsyncMock(return_value=SimpleNamespace(nodes=[], edges=[], episodes=[])))
        with mock.patch.object(graph,'_get_graph',new=mock.AsyncMock(return_value=g)), \
             mock.patch.object(graph,'_resolve_episode_slugs',new=mock.AsyncMock(return_value={})), \
             mock.patch.object(graph,'FUSION',True), \
             mock.patch.object(wiki.idx,'search',return_value=[{'name':self.SOURCE,'score':.7}]), \
             mock.patch.object(wiki.idx,'get',return_value={'body':body}), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer':self.DRAFT,'confidence':'high','sources':[self.SOURCE],'escalate':False,
             })) as distill, mock.patch.object(graph,'_record_query'):
            result = await graph._graph_answer_impl('What approved exact-User-Agent method supports Infisical credential reads?')
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])
        sent = distill.call_args.args[1]
        self.assertNotIn('credential_validation_texts',sent)
        self.assertNotIn(self.NEGATIVE,sent)
        self.assertNotIn('Background context. Background context.',sent)
        self.assertLess(len(sent), 4000)

    def test_missing_full_validation_body_cannot_use_positive_excerpt_fallback(self):
        p = {'facts':[], 'entities':[], 'source_excerpt':None,
             'semantic_notes':[{'name':self.SOURCE,'content':self.POSITIVE}],
             'credential_validation_texts':{self.SOURCE:''}}
        result,_,_=graph._validate_distilled_answer({
            'answer':self.DRAFT,'confidence':'high','sources':[self.SOURCE],
        }, p, QUESTION)
        self.assertIsNone(result['answer'])

    def test_complete_source_scope_only_uses_raw_cited_keys(self):
        p = {'credential_validation_texts':{self.SOURCE:self.POSITIVE,'unused':self.NEGATIVE}}
        self.assertEqual(graph._credential_cited_texts(p,[self.SOURCE]),{self.SOURCE:self.POSITIVE})
        self.assertEqual(graph._credential_cited_texts(p,['unknown']),{'unknown':''})

    async def test_current_distiller_packet_also_excludes_server_validation_text(self):
        p = {'facts':[], 'entities':[], 'source_excerpt':None,
             'semantic_notes':[{'name':self.SOURCE,'content':self.POSITIVE}],
             'credential_validation_texts':{self.SOURCE:self.POSITIVE}}
        p['temporal_context']=graph._temporal_context(p)
        text = str(graph._current_evidence_packet(p))
        self.assertNotIn('credential_validation_texts',text)


class EveryCitedCredentialBodyRequiredTests(unittest.IsolatedAsyncioTestCase):
    POSITIVE = '`vault-reader` is the supported installed live exact-User-Agent method for Infisical credential reads.'
    DRAFT = 'Use `vault-reader` for live Infisical credential reads.'

    def packet(self, count=2):
        sources = [f'2026-10-13-12000{i}-infisical-method' for i in range(count)]
        p = {'facts': [], 'entities': [], 'source_excerpt': None,
             'semantic_notes': [{'name': source, 'content': self.POSITIVE} for source in sources],
             'credential_validation_texts': {source: self.POSITIVE for source in sources}}
        return sources,p

    async def answer(self,p,sources,question=QUESTION):
        with mock.patch.object(graph,'_assemble_packet',new=mock.AsyncMock(return_value=p)), \
             mock.patch.object(graph,'_distill',new=mock.AsyncMock(return_value={
                 'answer':self.DRAFT,'confidence':'high','sources':sources,'escalate':False,
             })), mock.patch.object(graph,'_record_query'):
            return await graph._graph_answer_impl(question)

    async def test_one_complete_body_cannot_admit_another_missing_body(self):
        sources,p=self.packet()
        p['credential_validation_texts'][sources[1]]=''
        result=await self.answer(p,sources)
        self.assertIsNone(result['answer'])
        self.assertEqual(result['confidence'],'not_found')
        self.assertTrue(result['escalate'])
        self.assertIn('Complete cited source text is unavailable',result['superseded_note'])

    async def test_sixth_missing_body_is_checked_in_ordinary_answer_path(self):
        sources,p=self.packet(6)
        p['credential_validation_texts'][sources[-1]]=''
        result=await self.answer(p,sources,'What approved exact-User-Agent method supports Infisical credential reads?')
        self.assertIsNone(result['answer'])
        self.assertTrue(result['escalate'])

    async def test_whitespace_full_body_is_missing(self):
        sources,p=self.packet()
        p['credential_validation_texts'][sources[1]]='  \n '
        result=await self.answer(p,sources)
        self.assertIsNone(result['answer'])

    async def test_uncited_missing_candidate_does_not_block_complete_citation(self):
        sources,p=self.packet()
        p['credential_validation_texts'][sources[1]]=''
        result=await self.answer(p,[sources[0]])
        self.assertIsNotNone(result['answer'])
        self.assertTrue(result['escalate'])

    async def test_all_complete_bodies_preserve_positive_control(self):
        sources,p=self.packet()
        result=await self.answer(p,sources)
        self.assertIsNotNone(result['answer'])
        self.assertEqual(result['confidence'],'medium')
        self.assertTrue(result['escalate'])
