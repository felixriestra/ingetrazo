# CAM no IngeTrazo — guia de uso

**Extensões ▸ CAM…** transforma um desenho 2D sobre o material bruto em
código G para uma fresadora ou router com **GRBL** ou **LinuxCNC**. A
usinagem é 2,5D: contornos, furos e rebaixos cortados em camadas planas, na
vertical a partir da face superior do material. O torneamento com eixo
rotativo ainda não é suportado.

> **Experimental.** Os percursos são verificados antes da exportação, mas
> nenhuma verificação substitui os seus olhos. Execute cada programa novo
> primeiro como **corte no ar**, com o zero Z bem acima do material.
> Confira se a máquina vai aonde você espera.

## Início rápido: uma chapa com furos

1. Abra **Extensões ▸ CAM…** e clique em **Novo trabalho CAM…**. Primeiro
   dá-se nome ao arquivo: um trabalho CAM é seu próprio arquivo `.igcam`,
   não parte do modelo. Enquanto o trabalho está aberto o modelo fica de
   lado, e volta intacto.
2. **Prepare o trabalho** na aba **Trabalho**: largura, profundidade,
   espessura e material do material bruto, o **controlador**, as
   **unidades**, os limites da máquina e o **zero peça** (o ponto que você
   vai tocar na máquina: um dos nove pontos do material, na face superior
   ou na base). Depois clique em **Começar o trabalho**. Até lá nada mais
   fica disponível.
3. **Desenhe sobre o material** com as ferramentas de desenho: linhas,
   retângulos, círculos, arcos, polígonos, deslocamento, mover… A face
   superior do material é o chão, visto de cima. As ferramentas 3D
   (empurrar/puxar, siga-me, as de sólidos) ficam desligadas num trabalho
   CAM. Para aproveitar um modelo, selecione suas faces ou sua peça antes
   de abrir o CAM e clique em **Importar contornos do modelo**.
4. Confira as ferramentas na aba **Ferramentas**. Diâmetro, comprimento de
   corte, avanços e rotação do fuso devem ser os da sua fresa, não os de
   exemplo. **Da biblioteca…** adiciona uma das suas fresas com os avanços
   para este material.
5. Em **Operações**, escolha traçados e clique em **Adicionar operação**
   (veja abaixo).
6. Abra **Saída**. O trabalho é calculado sozinho e os percursos aparecem
   sobre o desenho. A verificação deve dizer **Nenhum problema encontrado**.
7. Clique em **Exportar código G…** e **Salvar** o trabalho (Arquivo ▸
   Salvar também salva). **Voltar ao modelo** fecha o trabalho.

## Operações

A aba **Operações** mostra os **traçados** do desenho e acompanha cada
alteração: um retângulo é um traçado fechado; linhas que se unem ponta com
ponta são um só traçado; um furo redondo é um círculo. Escolha traçados na
lista, ou clique em qualquer uma de suas arestas no desenho (o traçado
inteiro é escolhido e desenhado em laranja), e use **Adicionar operação**.
Traçados fechados dentro de outro são seus furos ou ilhas.

| Operação | A partir de | Corta |
|---|---|---|
| Perfil externo | um traçado fechado | por fora: a peça fica |
| Perfil interno | um traçado fechado (um furo) | por dentro: o furo sai |
| Rebaixo | um traçado fechado, com os traçados de dentro como ilhas | esvazia toda a área até uma profundidade |
| Furação | círculos | um furo por centro, com pica-pau se quiser |
| Gravação | traçados abertos ou fechados | segue a própria linha |
| Faceamento | nada (todo o material) | aplaina a face superior |
| Rebaixo aberto | um traçado fechado que toca a borda do material | como um rebaixo, mas saindo pelas bordas abertas: um rebaixo de borda, um entalhe |
| Mandrilamento | círculos | um furo redondo fresado com movimentos circulares, sem broca |
| Rasgo | um retângulo alongado, ou uma linha reta | um rasgo reto da largura do retângulo (ou da ferramenta) |
| Chanfro | traçados fechados ou abertos | um chanfro a 45° (ou no ângulo da fresa) na borda superior, com fresa em V |

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
- **Mandrilamento.** Qualquer furo redondo maior que a fresa: desce em
  espiral uma profundidade por passada por volta e termina a parede com um
  círculo plano. Os furos largos são limpos até o centro.
- **Rasgo.** A partir de um retângulo, o rasgo é esse retângulo, com o
  raio da fresa nos cantos internos. A partir de uma aresta reta, é um
  canal da largura da ferramenta, um raio mais longo em cada ponta.
- **Chanfro.** Precisa de uma **fresa em V** (de chanfro) na tabela de
  ferramentas (os trabalhos novos trazem uma de 90°). A *largura* é
  quanto se tira da borda. A profundidade para isso depende do ângulo:
  com 90°, a profundidade é igual à largura. Uma face dá um chanfro em
  volta do contorno e outro dentro de cada furo; *Na borda de um furo*
  troca o lado.
- **Rebaixo aberto.** As bordas que estão no contorno da chapa são
  detectadas e marcadas **abertas** sozinhas. A fresa as atravessa, e
  mantém seu raio em relação às outras bordas. *Bordas abertas* as lista
  por número; selecione a operação para ver os números (e as bordas
  abertas, em verde tracejado) no modelo.

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

**Simular…** (no cabeçalho do trabalho, ou em **Saída**) abre o trabalho
sobre um bloco de material em 3D e o reproduz em tempo de máquina. O
relógio segue a estimativa de tempo, com as trocas de ferramenta e a
partida do fuso.

- **Reprodução:** voltar ao início, reproduzir/pausar, **avançar** um
  comando, pular para o fim ou arrastar a barra para qualquer momento.
  Velocidades de 0,25× a 2000×.
- **O código G** do trabalho corre ao lado da vista, com a linha em
  execução destacada. Clique numa linha para levar o trabalho até ela.
- Sob a vista: a **fase** (desbaste, acabamento, rápido, entrada…), as
  coordenadas **X Y Z** da ponta da ferramenta, a operação, o volume
  removido e a ferramenta.
- **Qualidade:** *Prévia* mantém a reprodução fluida; *Alta qualidade* usa
  células mais finas para examinar a superfície acabada. O tamanho da
  célula aparece ao lado. **Simulação final** simula o trabalho inteiro em
  alta qualidade.
- **Abrir código G…** reproduz qualquer programa (de outro CAM, ou
  editado à mão) sobre o material deste trabalho; **Programa do trabalho**
  volta ao dele.

O material sai conforme a fresa passa. Os cortes passantes viram furos,
as pontes ficam de pé e o volume removido é contado. No desenho, os
percursos já cortados ficam fortes, os demais esmaecem e a fresa é
marcada onde está. Numa chapa grande, a malha fica mais grossa sozinha.

A simulação mostra o que o percurso faz. Não substitui o corte no ar na
máquina.

## A biblioteca de ferramentas

A aba **Ferramentas** tem as fresas de um trabalho. A **biblioteca de
ferramentas** é o seu armário de ferramentas, mantido entre trabalhos, com
as rotações e avanços conhecidos de cada fresa em cada material.

- **Da biblioteca…** abre a biblioteca. Escolha uma fresa e clique em
  **Adicionar ao trabalho**: ela entra na tabela de ferramentas do trabalho
  com sua rotação, avanço e mergulho **para o material e a máquina do
  trabalho**. O trabalho guarda uma cópia; mudar a biblioteca depois não
  muda um trabalho salvo.
- **Salvar na biblioteca** guarda a ferramenta selecionada: sua geometria, e
  sua rotação e avanços como *seus próprios dados* para este material. Da
  próxima vez a biblioteca devolve os seus valores.

**De onde vêm os valores.** Para cada fresa a biblioteca usa os melhores
dados que tem, e diz quais: seus próprios dados, os dados ou a tabela de
avanços do fabricante, ou uma **estimativa** a partir de uma tabela de
avanço por dente. As estimativas são marcadas com **≈**; as tabelas
incluídas são valores gerais, não dados testados. A linha abaixo da lista
explica cada ajuste: um plástico que derrete acima de certa rotação, a
faixa do fuso, os limites de avanço da máquina. **Confira uma estimativa
com um corte no ar antes de cortar.** O aço não tem dados na biblioteca:
digite você mesmo os avanços.

Uma fresa sem algumas medidas (comprimento de corte, comprimento total…)
aparece como *incompleta* e não pode entrar num trabalho até você
preenchê-las com **Editar…**. As ferramentas excluídas vão para a
**Lixeira…**, de onde podem ser restauradas.

**Catálogos de fabricantes.** **Importar catálogo…** lê a planilha CSV de um
fabricante: Sorotec e CMT (catálogo geral e série 193) vêm incluídos, e um
*perfil de catálogo* (JSON) adiciona outro fabricante. Cada linha é
conferida antes — um valor em polegadas numa coluna de milímetros, uma
fresa seis vezes maior que a haste, um avanço impossível — e mostrada como
nova, alterada, já existente ou rejeitada, com os motivos. Nada é
adicionado até você clicar em **Importar**, e **Desfazer a última
importação** retira tudo. Valores que você corrigiu à mão nunca são
sobrescritos por uma importação posterior.

**Arquivo da biblioteca.** A biblioteca é um arquivo na sua pasta de
usuário, com backups automáticos (os dez últimos). Num Mac com o 2DCam,
**Arquivo da biblioteca… ▸ Compartilhar a biblioteca do 2DCam** faz os dois
programas usarem a mesma.

## Salvar e reutilizar um trabalho

Um trabalho é seu próprio arquivo `.igcam`: a preparação, as ferramentas, o
desenho e as operações.

**Modelos de material.** **Salvar a preparação como modelo…** na aba
**Trabalho** guarda com um nome o material bruto, seu tipo, a máquina, o
controlador, as unidades e a tabela de ferramentas. **Novo a partir de um
modelo de material…** na página inicial do CAM começa um trabalho com um
deles: junto com os seus vêm incluídos algumas chapas e placas comuns. Um
modelo não tem desenho nem operações. **Salvar** (ou Arquivo ▸ Salvar) grava o arquivo;
**Trabalhos recentes** na página inicial do CAM e Arquivo ▸ Abrir o
reabrem. Toda alteração pode ser desfeita.

**Mude o desenho e as operações acompanham.** Mova, estique ou desloque um
traçado e cada operação feita com ele é refeita com o traçado novo e
recalculada, mantendo todos os seus ajustes. Desfazer traz os dois de
volta. Uma operação cujo traçado foi apagado, ou mudou tanto que já não dá
a mesma operação, é marcada com **⚠** e mantém sua última geometria até
você excluí-la ou adicioná-la de novo.

**Os traçados são verificados enquanto você desenha.** Um traçado que
cruza a si mesmo, ou que fica em parte ou todo fora do material, é marcado
com **⚠** na lista; a dica diz por quê.

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
