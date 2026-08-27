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

interface FileListProps {
  files: File[];
  onReorder: (files: File[]) => void;
  onRemove: (index: number) => void;
  onImageClick?: (src: string, alt: string) => void;
  focusedIndex?: number | null;
  onFocus?: (index: number | null) => void;
}

function SortableItem({
  file,
  index,
  onRemove,
  onImageClick,
  isFocused,
  onFocus,
}: {
  file: File;
  index: number;
  onRemove: (i: number) => void;
  onImageClick?: (src: string, alt: string) => void;
  isFocused: boolean;
  onFocus: (index: number | null) => void;
}) {
  const [preview, setPreview] = useState<string | null>(null);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: file.name + "-" + index });

  useEffect(() => {
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

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
      aria-label={`${file.name}, ${(file.size / 1024).toFixed(0)} KB`}
      onFocus={() => onFocus(index)}
      onBlur={() => onFocus(null)}
    >
      {preview && (
        <img
          src={preview}
          alt={file.name}
          className="file-thumb"
          onClick={(e) => {
            e.stopPropagation();
            if (onImageClick && preview) onImageClick(preview, file.name);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && onImageClick && preview) {
              onImageClick(preview, file.name);
            }
          }}
          tabIndex={0}
          role="button"
          aria-label={`Preview ${file.name}`}
        />
      )}
      <div className="file-info">
        <span className="file-name" title={file.name}>
          {file.name}
        </span>
        <span className="file-size">{(file.size / 1024).toFixed(0)} KB</span>
      </div>
      <button
        className="file-remove"
        onClick={(e) => {
          e.stopPropagation();
          onRemove(index);
        }}
        aria-label={`Remove ${file.name}`}
        onPointerDown={(e) => e.stopPropagation()}
      >
        ×
      </button>
    </div>
  );
}

export default function FileList({
  files,
  onReorder,
  onRemove,
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

    const oldIndex = files.findIndex(
      (_, i) => `${files[i].name}-${i}` === String(active.id),
    );
    const newIndex = files.findIndex(
      (_, i) => `${files[i].name}-${i}` === String(over.id),
    );
    if (oldIndex !== -1 && newIndex !== -1) {
      onReorder(arrayMove(files, oldIndex, newIndex));
    }
  };

  if (files.length === 0) return null;

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={files.map((f, i) => `${f.name}-${i}`)} strategy={rectSortingStrategy}>
        <div className="file-list" role="list" aria-label="Image files">
          {files.map((file, index) => (
            <SortableItem
              key={`${file.name}-${index}`}
              file={file}
              index={index}
              onRemove={onRemove}
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
