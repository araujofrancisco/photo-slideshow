import { useState, useEffect } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  rectSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

export interface SlideItem {
  file: File;
  duration: number;
  caption: string;
}

interface FileListProps {
  slides: SlideItem[];
  defaultDuration: number;
  onReorder: (slides: SlideItem[]) => void;
  onRemove: (index: number) => void;
  onMetaChange: (index: number, patch: Partial<SlideItem>) => void;
  onImageClick?: (src: string, alt: string) => void;
  focusedIndex?: number | null;
  onFocus?: (index: number | null) => void;
}

function SortableItem({
  slide,
  index,
  defaultDuration,
  onRemove,
  onMetaChange,
  onImageClick,
  isFocused,
  onFocus,
}: {
  slide: SlideItem;
  index: number;
  defaultDuration: number;
  onRemove: (i: number) => void;
  onMetaChange: (i: number, patch: Partial<SlideItem>) => void;
  onImageClick?: (src: string, alt: string) => void;
  isFocused: boolean;
  onFocus: (index: number | null) => void;
}) {
  const [preview, setPreview] = useState<string | null>(null);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: slide.file.name + "-" + index });

  useEffect(() => {
    const url = URL.createObjectURL(slide.file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [slide.file]);

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : undefined,
    opacity: isDragging ? 0.8 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`file-item ${isDragging ? "file-item-dragging" : ""} ${isFocused ? "file-item-focused" : ""}`}
      {...attributes}
      {...listeners}
      tabIndex={0}
      role="listitem"
      aria-label={`${slide.file.name}, ${(slide.file.size / 1024).toFixed(0)} KB`}
      onFocus={() => onFocus(index)}
      onBlur={() => onFocus(null)}
    >
      {preview && (
        <img
          src={preview}
          alt={slide.file.name}
          className="file-thumb"
          onClick={(e) => {
            e.stopPropagation();
            if (onImageClick && preview) onImageClick(preview, slide.file.name);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && onImageClick && preview) {
              onImageClick(preview, slide.file.name);
            }
          }}
          tabIndex={0}
          role="button"
          aria-label={`Preview ${slide.file.name}`}
        />
      )}
      <div className="file-info">
        <span className="file-name" title={slide.file.name}>
          {slide.file.name}
        </span>
        <span className="file-size">{(slide.file.size / 1024).toFixed(0)} KB</span>
        <div className="file-meta">
          <label className="file-meta-field" title="Seconds this image is shown">
            <span>Sec</span>
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={slide.duration}
              onChange={(e) => {
                const v = Number(e.target.value);
                onMetaChange(index, {
                  duration: Number.isFinite(v) && v > 0 ? v : defaultDuration,
                });
              }}
              onPointerDown={(e) => e.stopPropagation()}
              aria-label={`Seconds shown for ${slide.file.name}`}
            />
          </label>
          <input
            type="text"
            className="file-caption"
            placeholder="Caption (optional)"
            value={slide.caption}
            onChange={(e) => onMetaChange(index, { caption: e.target.value })}
            onPointerDown={(e) => e.stopPropagation()}
            aria-label={`Caption for ${slide.file.name}`}
          />
        </div>
      </div>
      <button
        className="file-remove"
        onClick={(e) => {
          e.stopPropagation();
          onRemove(index);
        }}
        aria-label={`Remove ${slide.file.name}`}
        onPointerDown={(e) => e.stopPropagation()}
      >
        ×
      </button>
    </div>
  );
}

export default function FileList({
  slides,
  defaultDuration,
  onReorder,
  onRemove,
  onMetaChange,
  onImageClick,
  focusedIndex,
  onFocus,
}: FileListProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = slides.findIndex(
      (_, i) => `${slides[i].file.name}-${i}` === String(active.id),
    );
    const newIndex = slides.findIndex(
      (_, i) => `${slides[i].file.name}-${i}` === String(over.id),
    );
    if (oldIndex !== -1 && newIndex !== -1) {
      onReorder(arrayMove(slides, oldIndex, newIndex));
    }
  };

  if (slides.length === 0) return null;

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={slides.map((s, i) => `${s.file.name}-${i}`)} strategy={rectSortingStrategy}>
        <div className="file-list" role="list" aria-label="Image files">
          {slides.map((slide, index) => (
            <SortableItem
              key={`${slide.file.name}-${index}`}
              slide={slide}
              index={index}
              defaultDuration={defaultDuration}
              onRemove={onRemove}
              onMetaChange={onMetaChange}
              onImageClick={onImageClick}
              isFocused={focusedIndex === index}
              onFocus={onFocus || (() => {})}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
