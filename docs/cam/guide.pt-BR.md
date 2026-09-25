# CAM no IngeTrazo — guia de uso

**Extensões ▸ CAM…** transforma o que você modela em código G para uma
fresadora ou router com **GRBL** ou **LinuxCNC**. A usinagem é 2,5D:
contornos, furos e rebaixos cortados em camadas planas, na vertical a
partir do plano de usinagem.

> **Experimental.** Os percursos são verificados antes da exportação, mas
> nenhuma verificação substitui os seus olhos. Execute cada programa novo
> primeiro como **corte no ar**, com o zero Z bem acima do material.
> Confira se a máquina vai aonde você espera.

## Início rápido: uma chapa com furos

1. Modele a peça como um sólido: uma chapa com seus furos e rebaixos.
   Transforme-a em grupo ou separe um componente em peças.
2. Selecione a peça no modelo ou na bandeja Peças.
3. Abra **Extensões ▸ CAM…**. Na aba **Trabalho**, clique em
   **Peça → operações**. O CAM adiciona todas as operações de que a peça
   precisa:
   - um **rebaixo** para cada furo cego, na sua profundidade;
   - **furação** para cada furo passante redondo que corresponda a uma
     broca da tabela de ferramentas;
   - um **perfil interno** para os demais furos passantes;
   - um **perfil externo** em volta do contorno, com quatro pontes.
4. Confira as ferramentas na aba **Ferramentas**. O diâmetro, o
   comprimento de corte, os avanços e a rotação devem ser os da sua fresa,
   não os de exemplo.
5. Escolha o **controlador**, as **unidades** e o **zero peça** na aba
   **Trabalho**. O zero peça é o ponto que você vai tocar na máquina: um
   dos nove pontos do material, na face superior ou na base.
6. Abra **Saída**. O trabalho é calculado sozinho e os percursos aparecem
   no modelo. A verificação deve dizer **Nenhum problema encontrado**.
7. Clique em **Exportar código G…**.

## Operações

Selecione faces, arestas ou uma peça e use
**Operações ▸ Adicionar a partir da seleção**.

| Operação | A partir de | Corta |
|---|---|---|
| Perfil externo | uma face, arestas fechadas, o contorno de uma peça | por fora: a peça fica |
| Perfil interno | furos de uma face, arestas fechadas | por dentro: o furo sai |
| Rebaixo | uma face com seus furos como ilhas, ou arestas fechadas | esvazia toda a área até uma profundidade |
| Furação | furos redondos (círculos) | um furo por centro, com pica-pau se quiser |
| Gravação | arestas abertas ou fechadas | segue a própria linha |
| Faceamento | nada (todo o material) | aplaina a face superior |

Cada operação tem uma ferramenta, uma profundidade e uma **profundidade
por passada**. Os demais ajustes dependem da operação:

- **Direção.** *Concordante* deixa o material à direita da fresa com o
  fuso girando no sentido horário. Costuma dar melhor acabamento numa
  máquina rígida. *Discordante* é o contrário.
- **Entrada.** *Mergulho vertical* desce reto. *Rampa* desce ao longo do
  corte e volta para limpar a cunha. *Hélice* desce em espiral, só onde
  cabe ao lado da peça; se não couber, usa rampa.
- **Sobremetal** e **Passes de acabamento.** Desbaste um pouco acima da
  medida e termine com um passe leve. Esse passe pode usar uma
  **ferramenta de acabamento** diferente.
- **Pontes.** Seguram a peça recortada na chapa. Informe o número, a
  largura e a altura. Corte-as depois com uma fresa de copiar ou um
  estilete.
- **Entrada e saída tangencial.** Começam e terminam o corte ao longo de
  uma borda, não em cima dela.
- **Compensação do raio.** *No programa* é o usual. *No controlador*
  escreve `G41`/`G42` e deixa a compensação para a tabela de ferramentas
  da máquina. Só funciona no LinuxCNC.
- **Furação.** O *pica-pau* fura em etapas e tira o cavaco entre elas. A
  *pausa* espera no fundo.

## Controladores

**GRBL** (GRBL 1.1, grblHAL, FluidNC). O GRBL não tem trocador de
ferramentas, então um trabalho com várias ferramentas é escrito como
**um arquivo por troca de ferramenta**, numerado na ordem de execução
(`chapa_01_T1_….nc`, `chapa_02_T3_….nc`…). Execute-os em ordem. Entre um
e outro, troque a ferramenta e **zere o eixo Z de novo**, porque cada
ferramenta tem o seu comprimento. Os ciclos de furação são escritos como
movimentos simples.

**LinuxCNC** (2.9 ou posterior). Um único arquivo `.ngc`. As trocas de
ferramenta usam `T# M6` com `G43 H#`, então os comprimentos vêm da tabela
de ferramentas da sua máquina. A furação usa ciclos fixos
(`G81`/`G82`/`G83`). A exportação também grava uma **tabela de ferramentas** (`.tbl`) ao
lado do programa, com os números e diâmetros das ferramentas do
trabalho: um `T# M6` de uma ferramenta que falta na tabela da máquina
interrompe o programa. Os comprimentos ficam em 0, para medir na
máquina. A tabela usa as unidades do trabalho, que devem ser as da
máquina.

Os dois aceitam milímetros (`G21`) ou polegadas (`G20`), conforme as
**Unidades** do trabalho. Os comentários saem no seu idioma, reduzidos a
ASCII.

## Simulação

**Saída ▸ Simular…** abre o trabalho sobre um bloco de material em 3D e
o reproduz em tempo de máquina. O relógio segue a estimativa de tempo,
com as trocas de ferramenta e a partida do fuso. Reproduza, pause,
arraste a barra para qualquer momento ou pule para o fim. A velocidade
vai até 500×.

O material sai conforme a fresa passa. Os cortes passantes viram furos,
as pontes ficam de pé e o volume removido é contado. Na janela do modelo,
os percursos já cortados ficam fortes, os demais esmaecem e a fresa é
marcada onde está. **Resolução** equilibra detalhe e velocidade. Numa
chapa grande, a malha fica mais grossa sozinha.

A simulação mostra o que o percurso faz. Não substitui o corte no ar na
máquina.

## O trabalho acompanha o modelo

O trabalho é salvo no `.igz` e toda alteração pode ser desfeita. Se você
alterar o modelo depois, clique em **Atualizar a partir da peça** na aba
**Trabalho**. As operações acompanham o novo contorno e os novos furos, e
mantêm seus ajustes. Uma operação que não corresponde mais mantém a
geometria anterior, e o CAM a nomeia.

## Verificação

Não é possível exportar enquanto a verificação indicar um erro:

- uma fresa que cortaria a peça (uma *penetração indevida*);
- um movimento rápido dentro do material ou através dele;
- um corte abaixo da base do material;
- uma profundidade maior que o comprimento de corte da ferramenta;
- uma rotação ou um avanço fora dos limites da máquina (ajustados na aba
  Trabalho).

Cada arquivo escrito é relido como o controlador o leria, e precisa
reproduzir o percurso com erro máximo de 0,001 mm.
