# Outputs Organization Update

## Date: November 28, 2025

## Changes Made

Updated the portfolio output system to organize all results in a dedicated `outputs/` folder with descriptive filenames.

### 1. Added `os` Module Import

```python
import os
```

### 2. Updated `curva_equity_dataframe()` Method

**What it does now:**
- Creates `outputs/` folder automatically if it doesn't exist
- Generates descriptive filename: `{PAIR}_equity_curve_{TIMESTAMP}.png`
- Saves high-resolution plot (300 DPI) to outputs folder
- Displays interactive plot window that stays open
- Includes pair name in plot title

**Example filename:**
```
outputs/LINK_AVAX_equity_curve_20251128_143052.png
```

**Features:**
- **Pair identification**: Uses trading pair names (e.g., LINK_AVAX)
- **Timestamp**: YYYYMMDD_HHMMSS format
- **High quality**: 300 DPI for publication-ready charts
- **Styled plot**: 12x6 figure, bold title, grid, proper labels

### 3. Updated `output_resumen_estadisticas()` Method

**What it does now:**
- Creates `outputs/` folder automatically if it doesn't exist
- Generates descriptive filename: `{PAIR}_equity_{TIMESTAMP}.csv`
- Saves equity DataFrame with all statistics to outputs folder
- Prints confirmation message with full path

**Example filename:**
```
outputs/LINK_AVAX_equity_20251128_143052.csv
```

**CSV Contents:**
- Time index
- All symbol holdings values
- Cash (Caja)
- Commissions
- Total portfolio value
- Returns (retornos)
- Equity curve (curva_equity)
- Maximum drawdown

## Benefits

✅ **Organized**: All outputs in one folder, not scattered in project root  
✅ **Traceable**: Each run has unique timestamp  
✅ **Identifiable**: Pair name in filename for easy identification  
✅ **Professional**: Publication-ready charts at 300 DPI  
✅ **Automated**: Folders created automatically, no manual setup  
✅ **Clean**: Project root stays clean, no clutter  

## File Naming Convention

```
outputs/{PAIR1}_{PAIR2}_equity_curve_{YYYYMMDD_HHMMSS}.png
outputs/{PAIR1}_{PAIR2}_equity_{YYYYMMDD_HHMMSS}.csv
```

### Examples:
- `outputs/LINK_AVAX_equity_curve_20251128_143052.png`
- `outputs/LINK_AVAX_equity_20251128_143052.csv`
- `outputs/SOL_XRP_equity_curve_20251128_150312.png`
- `outputs/SOL_XRP_equity_20251128_150312.csv`

## Usage

When you run your trading system:

1. **During execution**: System processes data and generates signals
2. **On completion**: Two files automatically saved:
   ```
   📊 Equity curve saved to outputs/LINK_AVAX_equity_curve_20251128_143052.png
   📄 Equity data saved to outputs/LINK_AVAX_equity_20251128_143052.csv
   ```
3. **Interactive display**: Plot window opens for immediate review
4. **Find outputs**: Check `outputs/` folder for all historical runs

## Directory Structure

```
MR_HFT_Python/
├── src/
│   ├── PortAQMHFT.py  ← Updated
│   └── ...
├── outputs/            ← New folder (auto-created)
│   ├── LINK_AVAX_equity_curve_20251128_143052.png
│   ├── LINK_AVAX_equity_20251128_143052.csv
│   ├── SOL_XRP_equity_curve_20251128_150312.png
│   └── SOL_XRP_equity_20251128_150312.csv
└── ...
```

## Tips

- **Compare runs**: Filenames with timestamps make it easy to track changes over time
- **Archive**: Move old runs to subfolders by date or strategy variant
- **Backup**: The outputs folder contains all your trading results
- **Share**: Charts are high-res and ready to share/present

## Code Changes Summary

**File Modified:** `src/PortAQMHFT.py`

**Lines Changed:**
1. Import section: Added `import os`
2. `curva_equity_dataframe()`: Added folder creation, descriptive naming, path handling
3. `output_resumen_estadisticas()`: Added folder creation, descriptive naming, path handling

**New Features:**
- Automatic folder creation with `os.makedirs(output_dir, exist_ok=True)`
- Dynamic filename generation with pair names and timestamps
- Path construction with `os.path.join()` for cross-platform compatibility
- User feedback with print statements showing full file paths
