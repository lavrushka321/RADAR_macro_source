import cv2
import numpy as np

def remove_background(input_path, output_path):
    # Загружаем изображение
    img = cv2.imread(input_path)
    if img is None:
        raise FileNotFoundError(f"Не найден файл: {input_path}")

    # Прямоугольник, в котором "точно" находится объект (отступ 10% от краёв)
    h, w = img.shape[:2]
    margin = 0.1
    rect = (
        int(w * margin),
        int(h * margin),
        int(w * (1 - 2 * margin)),
        int(h * (1 - 2 * margin)),
    )

    # Маски для GrabCut
    mask = np.zeros((h, w), np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    # Запускаем GrabCut (5 итераций — достаточно)
    cv2.grabCut(img, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)

    # Всё, что не "точно фон" — считаем объектом
    mask2 = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")

    # Слегка сглаживаем края маски
    mask2 = cv2.GaussianBlur(mask2, (5, 5), 0)

    # Собираем RGBA-изображение
    b, g, r = cv2.split(img)
    rgba = cv2.merge([b, g, r, mask2])

    cv2.imwrite(output_path, rgba)
    print(f"Готово: {output_path}")


if __name__ == "__main__":
    remove_background("input.png", "output.png")