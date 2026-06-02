"""
Pre-populate the local cache with common paint/coating chemicals.
Run this once when the server has internet access.
Usage:  python3 seed_chemicals.py
"""

from app import create_app
from app.pubchem import lookup_chemical

COMMON_PAINT_CHEMICALS = [
    # Pigments & extenders
    ('Titanium dioxide', '13463-67-7'),
    ('Zinc oxide', '1314-13-2'),
    ('Carbon black', '1333-86-4'),
    ('Iron oxide red', '1309-37-1'),
    ('Iron oxide yellow', '51274-00-1'),
    ('Calcium carbonate', '1317-65-3'),
    ('Barium sulfate', '7727-43-7'),
    ('Talc', '14807-96-6'),
    ('Kaolin', '1332-58-7'),
    ('Silica amorphous', '7631-86-9'),
    # Solvents
    ('Mineral spirits', '8052-41-3'),
    ('Xylene', '1330-20-7'),
    ('Toluene', '108-88-3'),
    ('Acetone', '67-64-1'),
    ('Ethanol', '64-17-5'),
    ('Isopropanol', '67-63-0'),
    ('n-Butanol', '71-36-3'),
    ('Ethyl acetate', '141-78-6'),
    ('Methyl ethyl ketone', '78-93-3'),
    ('2-Butoxyethanol', '111-76-2'),
    ('Propylene glycol', '57-55-6'),
    ('Naphtha', '64742-48-9'),
    # Additives
    ('Ammonia solution', '1336-21-6'),
    ('Zinc phosphate', '7779-90-0'),
    ('Lead chromate', '7758-97-6'),
    ('Stoddard solvent', '8052-41-3'),
]


def seed():
    app = create_app()
    with app.app_context():
        total = len(COMMON_PAINT_CHEMICALS)
        for i, (name, cas) in enumerate(COMMON_PAINT_CHEMICALS, 1):
            print(f"[{i:2d}/{total}] Looking up: {name} ({cas}) ... ", end='', flush=True)
            result = lookup_chemical(cas)  # CAS lookup is more precise
            if result.get('found'):
                print(f"OK — {result.get('name', '')}")
            else:
                # Try by name if CAS fails
                result = lookup_chemical(name)
                if result.get('found'):
                    print(f"OK (by name) — {result.get('name', '')}")
                else:
                    print("NOT FOUND")

    print("\nDone. Cache populated.")


if __name__ == '__main__':
    seed()
