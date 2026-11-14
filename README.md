# 3d2lasercut

Transforma um modelo 3D (STL, OBJ, 3MF, PLY…) em **peças para corte a laser** em MDF, acrílico ou qualquer material em chapa, em três modos:

- **Fatias empilhadas** (`stack`): o modelo vira camadas da espessura da chapa, com furos para **pinos de alinhamento**.
- **Grade encaixada** (`waffle`): fatias verticais em X e Y com **fendas de meia altura** que se encaixam, sem cola, usando bem menos material.
- **Placas + estrutura** (`plates`): junta estética e resistência.
  - As **faces planas** viram placas inteiras, unidas por **finger joints**.
  - As **superfícies curvas simples** (abóbadas, cilindros, cantos arredondados) viram **chapas flexíveis** (*kerf bending*).
  - Por dentro, uma grade de **nervuras** se prende às placas por abas.

Em todos os modos, o programa grava o **nome de cada peça**, compensa o **kerf** do laser e distribui tudo em **chapas**, encaixando as peças pelo **contorno real**. O resultado sai pronto para LightBurn, RDWorks, LaserGRBL etc.

| Fatias empilhadas | Grade encaixada |
|---|---|
| ![Prévias do empilhamento](docs/previas.png) | ![Prévias da grade](docs/grade_previas.png) |

![Chapa da grade do vaso: peças encaixadas pelo contorno, com o nome da peça parceira ao lado de cada fenda](docs/grade_chapa_vaso.png)

**Modo placas.** Placas (bege) por fora, chapa flexível (marrom) na abóbada e nervuras (azul) por dentro:

![Arco no modo placas: montado, visto por baixo e com as placas transparentes](docs/placas_arco.png)

![Caixa arredondada: cantos em chapa flexível e nervuras internas](docs/placas_caixa_arredondada.png)

![Chapa de corte do arco: placas com dedos, nervuras com fendas e a chapa flexível com o padrão de cortes](docs/placas_chapa_arco.png)

**Arredondamentos removidos.** O modelo de impressão 3D, todo arredondado (à esquerda), vira uma caixa de cantos vivos com dedos nas 12 arestas (no centro, montada; à direita, explodida):

![Caixa arredondada: original, placas montadas com cantos vivos e vista explodida com os dedos](docs/arredondamentos.png)

> **Status: v0.5.** Novidade: **arredondamentos e chanfros viram cantos vivos** automaticamente (ideal para peças feitas para impressão 3D). Agora também como **workbench do FreeCAD**, com prévia ao vivo. Veja [Usar no FreeCAD](#usar-no-freecad) e o [roteiro](#roteiro).

## Instalação

```bash
git clone https://github.com/neepleman/3d2lasercut && cd 3d2lasercut
pip install -e .
```

Requer Python 3.10+. As dependências são `trimesh`, `shapely`, `ezdxf`, `numpy`, `scipy`, `networkx` e `mapbox_earcut`.

## Usar no FreeCAD

O workbench **3D2LaserCut** abre um painel lateral com todas as opções. A prévia aparece **ao vivo** na vista 3D, sobre o seu modelo:

- placas e camadas em bege, chapas flexíveis em marrom, nervuras em azul;
- um controle de **vista explodida** para ver os encaixes;
- lista de placas para marcar as **de vitrine** (sem abas aparentes). Clicar numa placa na vista 3D seleciona a linha dela na lista;
- **Gerar chapas** desenha a distribuição das peças ao lado do modelo: vermelho = corte, azul = gravação;
- **Exportar…** grava SVG, DXF, prévia STL e relatório numa pasta.

Requer FreeCAD 1.0 ou mais novo (testado para a 1.1).

### Instalação (Windows)

1. Descubra a pasta de addons do seu FreeCAD. No FreeCAD, abra **Exibir → Painéis → Console Python** e digite:
   ```python
   print(App.getUserAppDataDir() + "Mod")
   ```
   Ela costuma ser algo como `C:\Users\<você>\AppData\Roaming\FreeCAD\v1-1\Mod`.
2. Ligue o seu clone do projeto a essa pasta com uma *junction*. Não precisa de administrador, e um `git pull` no clone já atualiza o FreeCAD. No PowerShell, troque `<pasta Mod>` pelo caminho do passo 1:
   ```bash
   New-Item -ItemType Directory -Force "<pasta Mod>"
   ```
   ```bash
   New-Item -ItemType Junction -Path "<pasta Mod>\3d2lasercut" -Target "C:\Users\<você>\3d2lasercut"
   ```
   Alternativa: no **Gerenciador de Addons**, adicione `https://github.com/neepleman/3d2lasercut` (branch `main`) como repositório personalizado e instale por lá.
3. Reinicie o FreeCAD e escolha o workbench **3D2LaserCut** na lista.
4. Na primeira vez, clique em **Instalar dependências** (ou ele pergunta sozinho). O botão instala `trimesh`, `shapely`, `ezdxf` e o que mais faltar, dentro do Python do FreeCAD, numa pasta do usuário. O numpy do FreeCAD não é alterado.

### Uso

1. Selecione um sólido (Part/PartDesign, STEP importado) ou uma malha (STL importado).
2. Clique em **Preparar para corte a laser**.
3. Ajuste o modo e as opções no painel. A prévia se atualiza sozinha; desmarque "Atualizar sozinho" para modelos pesados.
4. **Gerar chapas** e depois **Exportar…**.
5. **OK** mantém a prévia no documento; **Cancelar** apaga a prévia e volta a mostrar o modelo.

### Se algo der errado

- **Erros:** aparecem no próprio painel. Os detalhes técnicos ficam em **Exibir → Painéis → Relatório**.
- **"Instalar dependências" falhou:** dá para instalar à mão pelo PowerShell. Ajuste o caminho do FreeCAD e a pasta (`py311` = Python 3.11, que é o do FreeCAD 1.1):
  ```bash
  & "C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install --no-deps --upgrade --target "$env:APPDATA\FreeCAD\v1-1\AdditionalPythonPackages\py311" trimesh shapely ezdxf networkx scipy mapbox_earcut pyparsing typing_extensions fonttools
  ```

## Uso rápido (linha de comando)

```bash
3d2lasercut modelo.stl -t 3 --sheet 600x400 -o saida
```

| Opção | O que faz | Padrão |
|---|---|---|
| `-t, --thickness` | espessura **medida** da chapa (mm) | 3 |
| `-m, --mode` | `stack` (fatias empilhadas), `waffle` (grade encaixada) ou `plates` (placas + estrutura) | stack |
| `-a, --axis` | eixo vertical do modelo (empilhamento): `x`, `y`, `z` | z |
| `-s, --scale` / `-H, --height` | fator de escala, ou altura final desejada (mm) | 1 |
| `-k, --kerf` | largura do corte do laser (mm) | 0.15 |
| `--sheet` | tamanho da chapa `LxA` (mm) | 600x400 |
| `--margin`, `--spacing` | margem da chapa e espaço entre peças (mm) | 5, 3 |
| `--pin-diameter` | diâmetro da cavilha/palito (mm) | 3 |
| `--pin-clearance` | folga do furo (mm) | 0.1 |
| `--pin-margin` | parede mínima entre furo e borda (mm) | 2 |
| `--no-pins`, `--no-labels` | desliga pinos / etiquetas | — |
| `--min-area` | descarta ilhas menores que isso (mm²) | 4 |
| `--min-feature` | remove detalhes mais finos que isso (mm) | desligado |
| `--format` | `svg`, `dxf` ou `svg,dxf` | svg,dxf |
| `--no-rotate` | não gira as peças no nesting | — |

Exemplo: um vaso com 15 cm de altura, em MDF de 2,8 mm medido no paquímetro, chapa de 400×300:

```bash
3d2lasercut vaso.stl -H 150 -t 2.8 --sheet 400x300 -o vaso_corte
```

O mesmo vaso como grade encaixada, com fatias a cada 20 mm:

```bash
3d2lasercut vaso.stl -m waffle --grid 20 -H 150 -t 2.8 --sheet 400x300 -o vaso_grade
```

### Opções do modo grade (`-m waffle`)

| Opção | O que faz | Padrão |
|---|---|---|
| `--grid` | espaçamento entre fatias: `25` (X e Y iguais) ou `20x30` | 25 |
| `--slot-clearance` | folga na largura das fendas (mm) | 0.1 |

### Opções do modo placas (`-m plates`)

| Opção | O que faz | Padrão |
|---|---|---|
| `--ribs` | nervuras internas: `auto`, `none`, `on` ou o espaçamento em mm (ex.: `40`) | auto |
| `--no-tabs` | placas que ficam à vista e **não** devem ter furos de aba, ex.: `P1,P2` | — |
| `--finger-width` | largura dos dedos das juntas (mm) | 3 × espessura |
| `--max-fillet` | remove arredondamentos e chanfros até esse raio, estendendo as paredes até a aresta viva (`0` = não remove) | 10 |
| `--no-skins` | não cobrir as superfícies curvas com chapa flexível | — |
| `--plate-min-width` | faces planas mais estreitas que isso não viram placa (mm) | 12 |

No modo `auto`, as nervuras entram quando o modelo tem regiões sem cobertura (curvas duplas, como esferas) ou passa de 10 cm. O relatório informa o motivo.

Fluxo sugerido: rode uma vez, veja no relatório a tabela de placas (tamanho, vizinhas e se têm abas aparentes) e rode de novo com `--no-tabs` nas faces "de vitrine".

```bash
3d2lasercut arco.stl -m plates --ribs 30 --no-tabs P1,P2 -o arco_placas
```

### Opções de nesting

| Opção | O que faz | Padrão |
|---|---|---|
| `--nesting` | `shape` = pelo contorno real, `shelf` = por retângulos (mais rápido) | shape |
| `--nest-res` | resolução da grade do nesting (mm): menor aperta mais, maior é mais rápido | 1.0 |

## O que sai

| Arquivo | Conteúdo |
|---|---|
| `*_chapaNN.svg` / `.dxf` | uma chapa por arquivo. **Vermelho / camada `CORTE`**: furos e contornos, com os furos primeiro. **Azul / camada `GRAVACAO`**: nomes das peças e, na grade, o nome da peça parceira ao lado de cada fenda. A camada `CHAPA` (cinza) é só referência e não deve ser cortada. |
| `*_previa.stl` | o modelo montado (pilha ou grade) como sólido; abra num visualizador 3D para conferir antes de cortar |
| `*_relatorio.md` | resumo, metros de corte, aproveitamento de cada chapa, lista de **cavilhas com comprimento** (pilha) e instruções de montagem |

## Como funciona

### Fatias empilhadas

1. **Preparação.** O modelo é orientado pelo eixo escolhido, escalado e apoiado em Z=0. Malhas abertas recebem um reparo básico.
2. **Fatiamento.** O número de camadas é `round(altura / espessura)`, com cada seção tirada no meio da camada. Cada corpo do arquivo é fatiado separadamente e depois unido, então corpos sobrepostos não viram buracos.
3. **Limpeza.** Remove ilhas minúsculas e detalhes finos demais e simplifica os contornos.
4. **Pinos.** Ficam onde duas camadas vizinhas se sobrepõem, com parede mínima até a borda. Os pinos continuam pelas camadas enquanto couberem, o que dá cavilhas longas e passantes. Cada região de contato recebe 2 pinos afastados, para impedir rotação.
5. **Etiquetas.** Número da camada, com sufixo `-1`, `-2`… quando a camada tem várias ilhas. É gravado com fonte de traço e sublinhado. O sublinhado distingue 6 de 9 e indica a face de cima.
6. **Kerf.** O contorno externo cresce kerf/2 e os furos encolhem kerf/2. A peça cortada sai na medida do modelo.
7. **Nesting**, descrito abaixo.

### Grade encaixada

1. **Fatias.** São planos verticais `x = a` e `y = b`, centrados no modelo, com espaçamento `--grid`. As fatias das pontas ficam a pelo menos meio espaçamento da borda.
2. **Cruzamentos.** Para cada par de fatias X/Y, o programa calcula onde as duas têm material na **faixa da largura da fenda**, e não só na linha central, porque a chapa tem espessura e as bordas curvas se chocariam.
3. **Fendas de meia altura.** Uma peça é aberta por cima até o meio do cruzamento e a outra por baixo. A direção é escolhida **por par de peças**, de forma a remover o mínimo de material. Isso importa quando uma fatia é dividida em duas pelo modelo: a parte de cima encaixa por cima, sem rasgar a peça vizinha inteira.
4. **Etiquetas.** Cada peça leva o próprio nome (X1, Y2, X1-2…) e, ao lado de cada fenda, o nome da peça que encaixa ali.
5. **Kerf.** As fendas encolhem kerf/2 de cada lado e saem na largura certa: espessura + folga.

### Placas + estrutura

1. **Placas.** Faces coplanares e vizinhas formam uma placa. Só viram placa as faces com área e largura mínimas, o que descarta as facetas finas de superfícies curvas. Cada placa fica **para dentro** do modelo, com a face externa coincidindo com a superfície original, e as medidas externas se mantêm.
2. **Arredondamentos e chanfros** de até `--max-fillet` mm são removidos antes das juntas:
   - Cada face curva ou chanfrada entre duas placas é reconhecida pela normal ("entre" as normais das duas paredes) e pela posição. Ela também precisa encostar nas **duas** paredes; um trecho de curva maior não conta.
   - As duas placas são estendidas até a **aresta viva** (a interseção dos planos delas), e a aresta é levada até o canto, onde encontra uma terceira placa.
   - As placas nas pontas têm o canto do contorno completado (canto externo) ou recortado (canto interno).
   - A aresta viva vira uma junta normal, com dedos. Curvas maiores continuam como chapas flexíveis.
3. **Juntas** nas arestas entre placas:
   - **90° convexo (quina):** finger joint. A placa de menor número fica com os dedos das pontas, o que resolve os cantos onde 3 placas se encontram.
   - **90° côncavo (canto interno):** os dedos avançam além da aresta para preencher o canto. Nas pontas, recuam se houver uma terceira placa.
   - **Outros ângulos:** junta de topo. As placas recuam o necessário para não se invadir nem sair do modelo, e sobra uma fresta em V para cola ou massa.
4. **Chapas flexíveis.** O programa procura regiões curvas em que todas as normais são perpendiculares a um eixo (curvatura simples).
   - A seção transversal é planificada pelo comprimento de arco, medido no lado côncavo, que não estica.
   - A chapa recebe o padrão clássico de cortes alternados (*living hinge*).
   - Ela se prende com abas nas placas das pontas e é recortada onde encontraria outras placas.
   - Superfícies de dupla curvatura (esferas) ficam a cargo das nervuras.
5. **Nervuras.** São fatias X/Y como no modo grade, mas recortadas para não invadir as placas nem as chapas flexíveis. O recorte considera a espessura da nervura nas duas faces e no meio.
   - Onde encostam numa placa perpendicular, as nervuras ganham **abas** que atravessam **furos** na placa (*tab-and-slot*).
   - As abas evitam os cruzamentos entre nervuras e as pontas das placas.
6. **Etiquetas** na **face de dentro** de cada placa, escondidas depois de montar.

Os testes montam todas as peças em 3D (booleanas com `manifold3d`) e verificam que **nenhuma peça invade outra nem sai do modelo**: caixa, degrau em L, faces inclinadas, arco, caixa arredondada e cilindro.

### Nesting pelo contorno real

Cada peça é rasterizada numa grade, já dilatada por metade do espaçamento. Uma correlação via FFT encontra, de uma só vez, todas as posições livres da chapa. Para cada peça, o programa testa 4 rotações e escolhe a posição mais baixa e mais à esquerda, colocando as maiores primeiro. A dilatação é conservadora: o espaçamento mínimo entre peças é sempre respeitado. Peças côncavas se encaixam umas nas outras e peças pequenas podem cair dentro dos furos das grandes. Com `--nesting shelf`, volta o empacotamento por retângulos, mais rápido.

## Dicas

- **Meça a chapa.** MDF "3 mm" costuma ter entre 2,8 e 3,2 mm. A altura final da pilha é `camadas × espessura`.
- **Calibre o kerf e a folga** cortando antes uma peça de teste com um furo e um pino.
- **Chapa flexível:** o raio mínimo de curvatura aparece no relatório. Abaixo de ~4× a espessura, teste a flexão antes; o MDF levemente umedecido dobra melhor.
- **Placas:** as juntas usam a mesma folga das fendas (`--slot-clearance`). Calibre com um par de placas de teste.
- **Grade:** um espaçamento menor deixa a peça mais fiel e resistente, mas gasta mais material e tempo de corte. Comece com 3× a 8× a espessura da chapa.
- **Folga das fendas:** se o encaixe ficar duro, aumente `--slot-clearance`; se ficar frouxo, diminua.
- Escolha o **eixo** que deixa as fatias mais robustas. Por exemplo, deite uma figura alta se as pernas ficarem finas demais.
- As cavilhas podem ser palitos de churrasco (≈3 mm), varetas de bambu ou de madeira, ou pinos impressos.

## Desenvolvimento

```bash
pip install -e '.[dev]'
pytest
python examples/make_models.py        # gera modelos de teste em examples/models/
3d2lasercut examples/models/vaso.stl -o saida
```

O código fica no pacote Python `laserslicer/`. O núcleo não depende do FreeCAD, para poder ser testado e usado pela linha de comando. O workbench do FreeCAD (`freecad/lasercut3d/`) é uma interface fina por cima dele. Toda a lógica do painel fica em `laserslicer/session.py` e é testada sem o FreeCAD.

Os testes do painel (`tests/test_v04_freecad.py`) rodam o painel de verdade em Qt (PySide6, sem tela), com uma simulação da API do FreeCAD (`tests/fake_freecad.py`). A integração visual com o FreeCAD real é testada à mão.

```
config.py    parâmetros            pins.py     pinos de alinhamento
mesh_io.py   carregar/orientar     parts.py    etiquetas + kerf
slicer.py    fatiar em camadas     waffle.py   grade encaixada (fendas)
plates.py    placas + juntas       skins.py    chapas flexíveis (kerf bending)
ribs.py      nervuras internas     nesting.py  distribuir nas chapas
pipeline.py  orquestra tudo        export.py   SVG, DXF, prévia, relatório
cli.py       linha de comando      font.py     fonte de traço p/ gravação
assembly.py  peças montadas em 3D  session.py  lógica do painel (sem FreeCAD/Qt)

freecad/lasercut3d/   workbench: init_gui, commands, panel, fcbridge, deps
```

## Roteiro

- [x] **v0.1**: fatias empilhadas, pinos, etiquetas, kerf, nesting por retângulo, SVG/DXF
- [x] **v0.2**: grade de fatias encaixadas (*waffle*) com fendas e etiquetas de montagem; nesting pelo contorno real
- [x] **v0.3**: modo placas com finger joints automáticos, chapas flexíveis (kerf bending) nas curvas simples e nervuras internas com abas
- [x] **v0.4**: workbench do FreeCAD, com painel de opções, prévia 3D ao vivo, vista explodida, placas de vitrine, prévia das chapas e exportação
- [x] **v0.5**: remoção de arredondamentos e chanfros, com as paredes estendidas até a aresta viva e unidas por dedos
- [ ] v0.6: reconhecer caixas, com configurações fixas (base, paredes e tampa fixa, removível ou deslizante), divisórias e paredes que se estendem (juntas em T com abas e fendas)
- [ ] nível de detalhe ajustável (controle no painel): peças pequenas e não estruturais são removidas ou incorporadas à vizinha (degraus e ressaltos achatados, faces estreitas absorvidas como chanfros, ilhas de nervura descartadas)
- [ ] v0.7: rebaixos (encaixe a meia espessura) gravados pela laser, com decomposição de peças planas grandes ou recortadas (cruz, L, T, molduras) em retângulos sobrepostos
- [ ] depois: cálculo em segundo plano (sem travar a interface), juntas móveis/dobradiças, placas a partir das faces exatas do sólido (B-rep)

## Licença

Copyright © 2026 Neepleman. Distribuído sob a [GNU LGPL v2.1 ou posterior](LICENSE), compatível com o FreeCAD.
