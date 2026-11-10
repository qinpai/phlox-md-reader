# 2 示例：化学结构式与反应式

## 2.1 结构式

写一个 `smiles` 代码块即可，题注会自动补分子式与分子量：

```smiles
CC(=O)Oc1ccccc1C(=O)O
```

```smiles
CN1C=NC2=C1C(=O)N(C)C(=O)N2C
```

## 2.2 反应式

反应 SMILES 的写法是「底物>>产物」，多组分用 `.` 连接：

```rxn
CC(=O)Cl.O=C([O-])c1ccccc1[O-].[Na+].[Na+]>>CC(=O)Oc1ccccc1C(=O)O
```

## 2.3 箭头条件只能写英文

> [!WARNING]
> Indigo 内置字库不含中日韩字符。箭头上的条件写成中文会渲染成方块 `□□`。

正确写法（以 `!` 开头的行是条件）：

```rxn
! pyridine, 0 °C
CC(=O)Cl.O=C([O-])c1ccccc1[O-].[Na+].[Na+]>>CC(=O)Oc1ccccc1C(=O)O
```

中文条件请写进正文或题注，例如：以吡啶作缚酸剂，0 °C 下滴加乙酰氯，得阿司匹林（乙酰水杨酸）。

## 2.4 多步反应不支持

`A>>B>>C` 会报 `SMILES loader: invalid character within atom description: '>'`。把每一步拆成单独的块。

## 2.5 没有安装 Indigo 会怎样

化学块会显示一行「化学渲染未启用」的错误提示，其余内容（目录、插图、公式、导出）完全不受影响。安装方法见 README 的「可选依赖」一节。