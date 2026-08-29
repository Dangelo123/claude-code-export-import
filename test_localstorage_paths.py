#!/usr/bin/env python3
"""
O Local Storage do app guarda caminho de arquivo -- uma chave
`cc-session-cwd-local_<id>` por sessao e blobs JSON com caminhos escapados.
Copiado cru de um Windows para um Linux, o app pede para confiar em
`D:\\Projeto`, que nao existe ali. Estes casos travam a reescrita.
"""
import unittest

import localstorage_paths as lp

BS = chr(92)
MAPA = {
    'D:' + BS + 'ClaudeCowork_MeepGreenfield': '/home/f/ClaudeCowork_MeepGreenfield',
    'C:' + BS + 'Users' + BS + 'LocalAdmin' + BS + 'Documents' + BS + 'GTD_Project':
        '/home/f/GTD_Project',
    'C:' + BS + 'Users' + BS + 'LocalAdmin': '/home/f',
}


class Reescritor(unittest.TestCase):
    def setUp(self):
        self.r = lp.construir_reescritor(MAPA, True)

    def test_caminho_cru(self):
        self.assertEqual(self.r('D:' + BS + 'ClaudeCowork_MeepGreenfield'),
                         '/home/f/ClaudeCowork_MeepGreenfield')

    def test_cauda_troca_de_separador(self):
        self.assertEqual(
            self.r('D:' + BS + 'ClaudeCowork_MeepGreenfield' + BS + 'src' + BS + 'A.cs'),
            '/home/f/ClaudeCowork_MeepGreenfield/src/A.cs')

    def test_caminho_escapado_em_json(self):
        entrada = '{"pasta":"D:' + BS * 2 + 'ClaudeCowork_MeepGreenfield' + BS * 2 + 'sub"}'
        self.assertEqual(self.r(entrada),
                         '{"pasta":"/home/f/ClaudeCowork_MeepGreenfield/sub"}')

    def test_barra_normal_tambem_casa(self):
        self.assertEqual(self.r('D:/ClaudeCowork_MeepGreenfield'),
                         '/home/f/ClaudeCowork_MeepGreenfield')

    def test_prefixo_mais_longo_vence(self):
        # C:\Users\LocalAdmin tambem casa, mas GTD_Project e mais especifico
        alvo = 'C:' + BS + 'Users' + BS + 'LocalAdmin' + BS + 'Documents' + BS + 'GTD_Project'
        self.assertEqual(self.r(alvo), '/home/f/GTD_Project')

    def test_caminho_fora_do_mapa_fica_intacto(self):
        self.assertEqual(self.r('E:' + BS + 'outro' + BS + 'coisa'),
                         'E:' + BS + 'outro' + BS + 'coisa')

    def test_texto_sem_caminho(self):
        self.assertEqual(self.r('{"pinnedOrder":["local_a","local_b"]}'),
                         '{"pinnedOrder":["local_a","local_b"]}')

    def test_nao_estraga_barra_que_nao_e_caminho(self):
        # a barra invertida aqui pertence a um escape, nao a um caminho
        entrada = '{"re":"' + BS * 2 + 'd+","p":"D:' + BS * 2 + 'ClaudeCowork_MeepGreenfield"}'
        saida = self.r(entrada)
        self.assertIn(BS * 2 + 'd+', saida)
        self.assertIn('/home/f/ClaudeCowork_MeepGreenfield', saida)


class Codificacao(unittest.TestCase):
    def test_latin1_ida_e_volta(self):
        b = lp._codifica('C:' + BS + 'x', lp.LATIN1)
        self.assertEqual(b[0], lp.LATIN1)
        self.assertEqual(lp._decodifica(b), ('C:' + BS + 'x', lp.LATIN1))

    def test_utf16_ida_e_volta(self):
        b = lp._codifica('sessao ' + chr(231), lp.UTF16)
        self.assertEqual(b[0], lp.UTF16)
        self.assertEqual(lp._decodifica(b), ('sessao ' + chr(231), lp.UTF16))

    def test_valor_vazio(self):
        self.assertEqual(lp._decodifica(b''), (None, None))

    def test_valor_binario_e_ignorado(self):
        texto, _ = lp._decodifica(bytes([9, 200, 201, 202]))
        self.assertIsNone(texto)


if __name__ == '__main__':
    unittest.main(verbosity=2)
