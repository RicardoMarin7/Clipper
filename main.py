"""Punto de entrada de Clipper.

Raíz de composición: es el único lugar donde se instancia la aplicación.
Ejecutar desde la raíz del proyecto:  python main.py
"""

from ui.app import ClipperApp


def main() -> None:
    app = ClipperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
