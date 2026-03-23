import  cv2
import glob
from pre_processing import preProcessing
import os

# myImage= cv2.imread('pngImgs/t2.png')
pngImages = glob.glob("pngImgs/*.PNG")
jpgImages = glob.glob("jpgImgs/*.JPG")
EjpgImages = glob.glob("jepgImgs/*.JPEG")
def main():
    print("From Main function")
    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    #PNG Images
    for png in pngImages:
        print(png)
        image = cv2.imread(png)
        returnImage =preProcessing(image, png)
        out_name = os.path.splitext(os.path.basename(png))[0] + "_processed" + os.path.splitext(png)[1].lower()
        cv2.imwrite(os.path.join(output_dir, out_name), returnImage)
        cv2.imshow(f'After Processed Image ${png}',returnImage)
        cv2.waitKey()
    # JPG Images
    for jpg in jpgImages:
        print(jpg)
        image = cv2.imread(jpg)
        returnImage = preProcessing(image, jpg)
        out_name = os.path.splitext(os.path.basename(jpg))[0] + "_processed" + os.path.splitext(jpg)[1].lower()
        cv2.imwrite(os.path.join(output_dir, out_name), returnImage)
        cv2.imshow(f'After Processed Image ${jpg}', returnImage)
        cv2.waitKey()
    #JPEG images
    for ejpg in EjpgImages:
        print(ejpg)
        image = cv2.imread(ejpg)
        returnImage = preProcessing(image, ejpg)
        out_name = os.path.splitext(os.path.basename(ejpg))[0] + "_processed" + os.path.splitext(ejpg)[1].lower()
        cv2.imwrite(os.path.join(output_dir, out_name), returnImage)
        cv2.imshow(f'After Processed Image ${ejpg}', returnImage)
        cv2.waitKey()

main()


# See PyCharm help at https://www.jetbrains.com/help/pycharm/
#------------------------------------------------------------
