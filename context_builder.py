# -*- coding: utf-8 -*-
"""Opis bieżącego projektu QGIS przekazywany modelowi AI.

Zawiera tylko to, co potrzebne do zaplanowania pracy: nazwy i identyfikatory
warstw, typy geometrii, układy współrzędnych, pola atrybutów. Źródła warstw są
oczyszczane z haseł, loginów, tokenów i pełnych ścieżek dysków (sanitize_source).
Przykładowe wartości atrybutów są wysyłane tylko po włączeniu tej opcji.
"""
import json

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMapLayer,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .constants import MAX_CONTEXT_CHARS, MAX_CONTEXT_FIELDS, MAX_CONTEXT_LAYERS
from .i18n import language_label, qgis_locale
from .utils import sanitize_source

_REMOTE_PROVIDERS = {"wfs", "oapif", "arcgisfeatureserver", "wms", "wcs", "arcgismapserver"}


def layer_info(layer, with_fields=True, with_samples=False):
    info = {
        "id": layer.id(),
        "name": layer.name(),
        "provider": layer.providerType(),
        "crs": layer.crs().authid() if layer.crs().isValid() else None,
        "source": sanitize_source(layer.source(), layer.providerType()),
    }
    if layer.crs().isValid() and layer.crs().isGeographic():
        info["crs_units"] = "stopnie"
    if not layer.isValid():
        info["valid"] = False  # np. brak połączenia z bazą – nie odpytujemy źródła
        if isinstance(layer, QgsVectorLayer):
            info["kind"] = "vector"
        else:
            info["kind"] = "raster" if isinstance(layer, QgsRasterLayer) else "inna"
        return info
    if isinstance(layer, QgsVectorLayer):
        info["kind"] = "vector"
        info["geometry"] = QgsWkbTypes.displayString(layer.wkbType())
        try:
            info["feature_count"] = int(layer.featureCount())
        except Exception:  # noqa: BLE001
            info["feature_count"] = -1
        if layer.selectedFeatureCount():
            info["selected"] = layer.selectedFeatureCount()
        if layer.subsetString():
            info["filter"] = layer.subsetString()[:200]
        if with_fields:
            fields = layer.fields()
            info["fields"] = ["%s (%s)" % (f.name(), f.typeName()) for f in list(fields)[:MAX_CONTEXT_FIELDS]]
            if fields.count() > MAX_CONTEXT_FIELDS:
                info["fields_truncated"] = fields.count()
            if with_samples and layer.providerType().lower() not in _REMOTE_PROVIDERS:
                info["samples"] = _samples(layer)
    elif isinstance(layer, QgsRasterLayer):
        info["kind"] = "raster"
        try:
            info["bands"] = layer.bandCount()
            info["size_px"] = [layer.width(), layer.height()]
        except Exception:  # noqa: BLE001
            pass
    else:
        info["kind"] = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "inna"
    return info


def _samples(layer, per_field=5, max_fields=15):
    out = {}
    try:
        if layer.featureCount() > 500000:
            return out
    except Exception:  # noqa: BLE001
        return out
    fields = layer.fields()
    for idx, f in enumerate(fields):
        if len(out) >= max_fields:
            break
        type_name = f.typeName().lower()
        is_text = f.type() == 10 or any(t in type_name for t in ("char", "string", "text"))  # 10 = QString
        if not is_text:
            continue
        try:
            vals = [str(v)[:40] for v in layer.uniqueValues(idx, per_field) if v not in (None, "")]
        except Exception:  # noqa: BLE001
            continue
        if vals:
            out[f.name()] = vals
    return out


def build_context(iface, settings):
    prj = QgsProject.instance()
    with_samples = settings.value("send_samples", False, bool)
    ctx = {
        "qgis_version": Qgis.version(),
        "ui_locale": qgis_locale(),
        "ui_language": language_label(),
        "python_allowed": settings.value("allow_python", False, bool),
        "project": {
            "crs": prj.crs().authid() if prj.crs().isValid() else None,
            "crs_geographic": bool(prj.crs().isValid() and prj.crs().isGeographic()),
        },
    }
    try:
        ext = iface.mapCanvas().extent()
        ctx["project"]["map_extent"] = [round(float(ext.xMinimum()), 2), round(float(ext.yMinimum()), 2),
                                        round(float(ext.xMaximum()), 2), round(float(ext.yMaximum()), 2)]
    except Exception:  # noqa: BLE001
        pass

    nodes = prj.layerTreeRoot().findLayers()
    layers = []
    for node in nodes[:MAX_CONTEXT_LAYERS]:
        lyr = node.layer()
        if lyr is None or not isinstance(lyr, QgsMapLayer):
            continue
        info = layer_info(lyr, with_fields=True, with_samples=with_samples)
        info["visible"] = bool(node.isVisible())
        layers.append(info)
    if len(nodes) > MAX_CONTEXT_LAYERS:
        ctx["layers_truncated"] = len(nodes)
    ctx["layers"] = layers
    active = iface.activeLayer()
    ctx["active_layer"] = active.name() if isinstance(active, QgsMapLayer) else None

    reg = QgsApplication.processingRegistry()
    ctx["processing_providers"] = sorted(p.id() for p in reg.providers() if p.isActive())
    plugin_algs = plugin_algorithms(reg)
    if plugin_algs:
        ctx["plugin_algorithms"] = plugin_algs
    try:
        from qgis.utils import active_plugins
        ctx["installed_plugins"] = sorted(active_plugins)[:150]
    except Exception:  # noqa: BLE001
        pass

    text = json.dumps(ctx, ensure_ascii=False)
    if len(text) > MAX_CONTEXT_CHARS:  # za duży projekt – skracamy opisy dalszych warstw
        for info in ctx["layers"][10:]:
            info.pop("fields", None)
            info.pop("samples", None)
            info.pop("source", None)
    ctx["layers"] = ctx.pop("layers", [])
    return ctx


# Dostawcy wbudowani w QGIS – ich algorytmy model zna; algorytmy wtyczek (np. QuickOSM) podajemy jawnie,
# żeby model nie zgadywał identyfikatorów.
_CORE_PROVIDERS = {"native", "qgis", "gdal", "3d", "pdal", "grass", "grass7", "grassprovider", "saga", "otb",
                   "model", "script", "project", "mdal"}
_MAX_PLUGIN_ALGS = 120
_MAX_PLUGIN_ALGS_CHARS = 6000


def plugin_algorithms(reg):
    """{id: "PARAMETRY -> WYJŚCIA"} dla algorytmów z aktywnych dostawców spoza rdzenia QGIS ("?" = opcjonalny)."""
    out, size = {}, 0
    for prov in sorted(reg.providers(), key=lambda p: p.id()):
        if not prov.isActive() or prov.id().lower() in _CORE_PROVIDERS:
            continue
        for alg in sorted(prov.algorithms(), key=lambda a: a.id()):
            try:
                params = [d.name() + ("?" if _optional(d) else "") for d in alg.parameterDefinitions()
                          if not _hidden(d) and not d.isDestination()]
                outs = [o.name() for o in alg.outputDefinitions()]
                desc = "%s -> %s" % (", ".join(params), ", ".join(outs) or "-")
            except Exception:  # noqa: BLE001
                desc = ""
            size += len(alg.id()) + len(desc) + 8
            if len(out) >= _MAX_PLUGIN_ALGS or size > _MAX_PLUGIN_ALGS_CHARS:
                return out
            out[alg.id()] = desc
    return out


def _optional(param):
    try:
        from qgis.core import Qgis
        flag = Qgis.ProcessingParameterFlag.Optional
    except AttributeError:
        from qgis.core import QgsProcessingParameterDefinition
        flag = QgsProcessingParameterDefinition.FlagOptional
    try:
        return bool(param.flags() & flag)
    except TypeError:
        return False


def _hidden(param):
    try:
        from qgis.core import Qgis
        flag = Qgis.ProcessingParameterFlag.Hidden
    except AttributeError:
        from qgis.core import QgsProcessingParameterDefinition
        flag = QgsProcessingParameterDefinition.FlagHidden
    try:
        return bool(param.flags() & flag)
    except TypeError:
        return False


def context_json(ctx):
    text = json.dumps(ctx, ensure_ascii=False, indent=1)
    if len(text) > MAX_CONTEXT_CHARS + 4000:
        text = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_CONTEXT_CHARS + 4000:
        raise ValueError("Opis projektu jest zbyt duży. Ogranicz liczbę warstw lub wyłącz przykładowe wartości.")
    return text
